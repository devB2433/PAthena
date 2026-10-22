"""A separate, bounded prose edit before business-result persistence."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from uuid import uuid4

import httpx

from .config import contained_file
from .domain import StageOutput
from .provider import OutputContractError, ProviderUnavailable, category, raise_failure
from .skills import SkillBundle, digest

EDITABLE = ('title', 'rationale', 'impact', 'recommendation')
PROTECTED = re.compile(
    r'`[^`]+`|“[^”]+”|「[^」]+」|https?://[^\s)>]+|'
    r'\b[\w.-]+(?:/[\w.:-]+)+\b|\b[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+(?::\d+(?:-\d+)?)?|'
    r'\b[A-Za-z][A-Za-z0-9_-]*\b|\d+(?:\.\d+)*(?:%|毫秒|秒|分钟|小时|天|年|次|行|位|字节)?|'
    r'不超过|不低于|未确认|尚未|未验证|无法|可能|部分|至少|至多|超过|低于|高于|仅|必须|不得|禁止|未|不')


def protected_fragments(text: str) -> list[str]:
    # Literal JSON-style line breaks and rendered line breaks carry the same
    # formatting outside code and quotations. Never normalize protected spans.
    chunks = re.split(r'(`[^`]+`|“[^”]+”|「[^」]+」)', text)
    protected = []
    for index, chunk in enumerate(chunks):
        if index % 2:
            protected.append(chunk)
        else:
            for escape in ('\\n', '\\r', '\\t'):
                chunk = chunk.replace(escape, ' ')
            protected.extend(PROTECTED.findall(chunk))
    return protected


def editor_skill(root: Path) -> SkillBundle:
    folder = root / 'chinese_output_editor'
    manifest = json.loads(contained_file(folder, 'manifest.json').read_text())
    if manifest.get('id') != 'chinese_output_editor' or manifest.get('version') != '1.0.0':
        raise ValueError('中文编辑技能版本未登记')
    source = json.loads(contained_file(folder, 'SOURCE.json').read_text())
    expected_refs = {'vendor/humanizer-zh/' + name: sha for name, sha in source['files'].items()}
    if manifest['references'] != expected_refs:
        raise ValueError('中文编辑技能必须包含完整固定原版和许可')
    body = contained_file(folder, 'SKILL.md').read_bytes()
    if digest(body) != manifest['sha256']:
        raise ValueError('中文编辑技能摘要不一致')
    packet = body
    for name, expected in sorted(expected_refs.items()):
        raw = contained_file(folder, name).read_bytes()
        if digest(raw) != expected:
            raise ValueError('中文编辑技能原版资源摘要不一致')
        packet += b'\n\n' + raw
    return SkillBundle('chinese_output_editor', manifest['version'],
                       digest(packet + contained_file(folder, 'SOURCE.json').read_bytes()), packet.decode(), ())


def editor_fingerprint(settings) -> str:
    return digest(editor_skill(settings.skills_dir).content_hash.encode() + Path(__file__).read_bytes()) if settings.humanize_chinese else 'disabled'


def slots_for(output: StageOutput):
    slots = {}
    fields = [(None, 'summary', output.summary)]
    fields += [(index, name, getattr(record, name)) for index, record in enumerate(output.records)
               for name in EDITABLE if hasattr(record, name)]
    for index, name, text in fields:
        if re.search(r'[\u3400-\u9fff]', text):
            slots[f'text_{len(slots):04d}'] = {'index': index, 'field': name, 'text': text,
                                             'protected': protected_fragments(text)}
    return slots


def apply_edits(output: StageOutput, slots: dict, texts: dict) -> StageOutput:
    if not isinstance(texts, dict) or set(texts) != set(slots):
        raise OutputContractError('中文编辑的文本槽库存不完整或越界')
    data = output.model_dump()
    for key, slot in slots.items():
        text = texts[key]
        if not isinstance(text, str) or not text.strip() or not re.search(r'[\u3400-\u9fff]', text):
            raise OutputContractError('中文编辑结果为空、类型错误或不是中文')
        if Counter(protected_fragments(text)) != Counter(slot['protected']):
            raise OutputContractError('中文编辑改动了技术片段、引文、数值或限定，禁止入库')
        target = data if slot['index'] is None else data['records'][slot['index']]
        target[slot['field']] = text
    try:
        return StageOutput.model_validate(data)
    except ValueError:
        raise OutputContractError('中文编辑结果不符合原有业务结果结构') from None


class OutputEditor:
    def __init__(self, settings, store, transport=None):
        self.settings, self.store, self.transport = settings, store, transport

    async def edit(self, parent_id: str, role: str, output: StageOutput) -> StageOutput:
        parent = self.store.task(parent_id)
        run = self.store.run(parent['run_id'])
        if run['language'] != 'zh-CN' or not self.settings.humanize_chinese:
            return output
        bundle = editor_skill(self.settings.skills_dir)
        slots = slots_for(output)
        if not slots:
            return output
        original_hash = digest(output.model_dump_json().encode())
        tid = self.store.add_task(run['id'], 'chinese_output_polish',
            {'source_task_id': parent_id, 'source_hash': original_hash,
             'skill_hash': bundle.content_hash, 'editor_hash': editor_fingerprint(self.settings)}, parent_id, 'presentation')
        task = self.store.task(tid)
        if task['status'] == 'SUCCEEDED':
            receipt = task['result']
            if receipt['source_hash'] != original_hash:
                raise OutputContractError('中文编辑检查点与原始产物不一致')
            return apply_edits(output, slots, receipt['texts'])
        self.store.start_task(tid, bundle, digest(bundle.instruction.encode()))
        try:
            # Bounded output groups. No group may alter another group's slots.
            texts, group, size = {}, {}, 0
            groups = []
            for key, slot in slots.items():
                if group and size + len(slot['text']) > 12000:
                    groups.append(group)
                    group, size = {}, 0
                group[key] = {'text': slot['text'], 'protected': slot['protected']}
                size += len(slot['text'])
            if group:
                groups.append(group)
            # Each successful batch is an immutable task result and is reused on resume.
            for index, group in enumerate(groups):
                batch = self.store.add_task(run['id'], 'chinese_output_polish_batch', {'index': index,
                    'input_hash': digest(json.dumps(group, ensure_ascii=False, sort_keys=True).encode())}, tid, 'presentation')
                prior = self.store.task(batch)
                if prior['status'] == 'SUCCEEDED':
                    result = prior['result']['texts']
                else:
                    self.store.start_task(batch, bundle, digest(bundle.instruction.encode()))
                    try:
                        result = await self.request(run['id'], batch, bundle, group)
                        apply_edits(output, {k: slots[k] for k in group}, result)
                        self.store.finish_service(batch, {'texts': result})
                    except BaseException:
                        self.store.fail_task(batch, '中文编辑批次未完成，原始分析结果已保留')
                        raise
                texts.update(result)
            edited = apply_edits(output, slots, texts)
            self.store.finish_service(tid, {'source_hash': original_hash, 'role': role,
                'texts': texts, 'edited_hash': digest(edited.model_dump_json().encode()),
                'changed_fields': sum(texts[k] != slots[k]['text'] for k in slots)})
            return edited
        except BaseException:
            self.store.fail_task(tid, '中文输出检查未完成，禁止提交业务结果')
            raise

    async def request(self, run_id: str, task_id: str, bundle: SkillBundle, group: dict):
        body = {'model': self.settings.model_id, 'max_tokens': self.settings.agent_output_tokens,
            'temperature': 0, 'messages': [{'role': 'system', 'content': bundle.instruction},
                {'role': 'user', 'content': json.dumps(group, ensure_ascii=False)}],
            'tools': [{'type': 'function', 'function': {'name': 'submit_chinese_edits', 'strict': True,
                'description': '提交检查优化后的完整中文文本槽，仅改变表达。', 'parameters': {
                    'type': 'object', 'properties': {'texts': {'type': 'object',
                        'properties': {k: {'type': 'string'} for k in group},
                        'required': list(group), 'additionalProperties': False}},
                    'required': ['texts'], 'additionalProperties': False}}}],
            'tool_choice': {'type': 'function', 'function': {'name': 'submit_chinese_edits'}}}
        reservation = self.store.reserve(run_id, task_id, len(json.dumps(body).encode()) // 3 + body['max_tokens'])
        actual = None
        logical = str(uuid4())
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=660, trust_env=False) as client:
                response = await client.post(self.settings.gateway + '/v1/chat/completions', json=body,
                    headers={'x-auditor-run': run_id, 'x-auditor-task': task_id, 'x-auditor-request': logical})
            self.store.save_model_output(task_id, response.text)
            failure = self.store.provider.failure(logical)
            if failure:
                raise_failure(failure)
            if response.status_code != 200:
                raise_failure(category(response.status_code))
            value = response.json()
            actual = value.get('usage', {}).get('total_tokens')
            choice = value['choices'][0]
            calls = choice['message'].get('tool_calls', [])
            if choice['finish_reason'] != 'tool_calls' or len(calls) != 1 or calls[0]['function']['name'] != 'submit_chinese_edits':
                raise OutputContractError('中文编辑没有完整提交约定工具结果')
            result = json.loads(calls[0]['function']['arguments'])
            if set(result) != {'texts'}:
                raise OutputContractError('中文编辑返回了未登记字段')
            return result['texts']
        except httpx.RequestError:
            raise ProviderUnavailable('中文输出编辑连接中断，原始分析草稿已保留', code='transport') from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise OutputContractError('中文编辑返回不符合结构化约定，原始返回已保存') from None
        finally:
            self.store.settle(reservation, actual)
