const CAPABILITY_NAME_LABELS: Record<string, string> = {
	'project.similar_search': '相似项目检索',
	'project.rule_check': '立项规则核对',
	'project.authorization.read': '授权项目资料读取',
	'planning.template_retrieve': '策划资料检索',
	'document.render': '文档生成与导出',
	'expert.user_data_lookup': '候选专家数据读取',
	'expert.user_conflict_check': '专家回避声明整理',
	'expert.user_rank': '专家候选排序',
	'meeting.cost_calculate': '会议费用核算',
	'meeting.material_generate': '会议物料生成',
	'meeting.material_check': '会前材料检查',
	'meeting.transcript_import': '会议转写文本导入',
	'opinion.text_import': '意见文本导入',
	'opinion.classify': '意见分类整理',
	'report.rag_retrieve': '授权报告资料检索',
	'archive.document_metadata': '归档文件信息整理',
	'archive.completeness_check': '归档完整性检查',
	'expense.invoice_import': '结构化票据信息导入',
	'expense.invoice_verify_dedupe': '票据重复核对',
	'expense.rule_check': '报销规则核对',
};

export function getTaskCapabilityDisplayName(capability: string): string {
	return CAPABILITY_NAME_LABELS[capability] ?? '模板必需能力';
}
