import messages from '../../../src/security_auditor/resources/messages.json';
export type Language = 'en' | 'zh-CN';
const catalog: Record<string, string> = messages;
const reverse = Object.fromEntries(Object.entries(catalog).map(([zh, en]) => [en, zh]));
export function translate(text: string, language: Language) {
  return language === 'zh-CN' ? reverse[text] || text : catalog[text] || text;
}
export function format(text: string, language: Language, values: Record<string, string | number>) {
  return translate(text, language).replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? ''));
}
