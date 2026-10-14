"""Lossless analytical projection: drop undeclared EMPTY decorations only.

Missing fields, nonempty unknown fields and all analytical values still fail
their original schema. The raw model response is retained separately.
"""
import copy

from .provider import OutputContractError


def resolve_claim_indices(value, role, targets):
    """Bind numeric claim addresses to exact immutable normative text."""
    result = copy.deepcopy(value)
    if role not in {'requirement_generator', 'requirement_reviewer'}:
        return result
    for record in result.get('records', []):
        if role == 'requirement_generator' and 'statement_source' in record:
            def bound(source, claim, index):
                if source.get('claim') and source['claim'] != claim:
                    raise OutputContractError('成对规范文本与来源文本冲突')
                if source.get('claim_index') is not None and source['claim_index'] != index:
                    raise OutputContractError('成对规范项与编号冲突')
                return {**source, 'claim':claim, 'claim_index':index}
            source = record.pop('statement_source')
            criteria = record['acceptance_criteria']
            record['source_obligations'] = [bound(source,record['statement'],0)] + [
                bound(item['source'],item['criterion'],i+1) for i,item in enumerate(criteria)]
            record['acceptance_criteria'] = [item['criterion'] for item in criteria]
        target = record if role == 'requirement_generator' else targets.get(record.get('subject_id'), {})
        claims = [target.get('statement'), *target.get('acceptance_criteria', [])]
        items = record.get('source_obligations' if role == 'requirement_generator' else 'claim_reviews', [])
        if role == 'requirement_reviewer' and isinstance(items, dict):
            slots = ['statement', *[f'criterion_{i}' for i in range(1,len(claims))]]
            if set(items) != set(slots):
                raise OutputContractError('固定来源复核槽位库存不完整或含未知项')
            items = [{**items[key], 'claim_index':i} for i,key in enumerate(slots)]
            record['claim_reviews'] = items
        indices = [item.get('claim_index') for item in items]
        if not claims[0] or any(type(index) is not int for index in indices) or sorted(indices) != list(range(len(claims))):
            raise OutputContractError('正文与验收项的编号库存不完整、重复或越界')
        for item in items:
            expected = claims[item['claim_index']]
            if item.get('claim') and item['claim'] != expected:
                raise OutputContractError('规范项编号与文本冲突')
            item['claim'] = expected
    return result


def project_empty_extras(value, schema):
    definitions = schema.get('$defs', {})
    removed = []

    def project(item, node, path):
        if '$ref' in node:
            node = definitions[node['$ref'].removeprefix('#/$defs/')]
        if isinstance(item, list) and 'items' in node:
            return [project(entry, node['items'], [*path, i]) for i, entry in enumerate(item)]
        if isinstance(item, dict) and node.get('additionalProperties') is False:
            properties = node.get('properties', {})
            result = {}
            for key, entry in item.items():
                if key not in properties and entry in (None, '', [], {}):
                    removed.append({'path': [*path, key], 'value': entry})
                else:
                    result[key] = project(entry, properties.get(key, {}), [*path, key])
            return result
        return copy.deepcopy(item)

    return project(value, schema, []), removed
