from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUT = Path("output/Agent与Prompt研究方向创新潜力比较.docx")


def set_run_font(run, name="Microsoft YaHei", size=None, bold=None, color=None, italic=None):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    if italic is not None:
        run.italic = italic


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def set_cell_borders(cell, color="D9D9D9", size="6"):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), "single")
        tag.set(qn("w:sz"), size)
        tag.set(qn("w:space"), "0")
        tag.set(qn("w:color"), color)


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def set_row_cant_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def set_table_widths(table, widths):
    table.autofit = False
    for row in table.rows:
        for idx, width in enumerate(widths):
            row.cells[idx].width = Inches(width)


def clear_paragraph(paragraph):
    for run in list(paragraph.runs):
        paragraph._p.remove(run._r)


def write_cell(cell, text, bold=False, color="000000", size=9.2, align=WD_ALIGN_PARAGRAPH.LEFT):
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = align
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.15
    r = p.add_run(str(text))
    set_run_font(r, size=size, bold=bold, color=color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    set_cell_margins(cell)
    set_cell_borders(cell)


def add_table(doc, headers, rows, widths, header_fill="1F4E79", body_fill="FFFFFF", alt_fill="F3F6FA", font_size=9.2):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    set_table_widths(table, widths)
    header = table.rows[0]
    set_repeat_table_header(header)
    for idx, h in enumerate(headers):
        write_cell(header.cells[idx], h, bold=True, color="FFFFFF", size=font_size, align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell_shading(header.cells[idx], header_fill)
    for ridx, row_values in enumerate(rows):
        row = table.add_row()
        set_row_cant_split(row)
        fill = body_fill if ridx % 2 == 0 else alt_fill
        for idx, value in enumerate(row_values):
            write_cell(row.cells[idx], value, size=font_size)
            set_cell_shading(row.cells[idx], fill)
    for row in table.rows:
        for cell in row.cells:
            set_cell_borders(cell)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def add_page_number(paragraph):
    run = paragraph.add_run()
    fld_char1 = OxmlElement("w:fldChar")
    fld_char1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char1)
    run._r.append(instr_text)
    run._r.append(fld_char2)
    set_run_font(run, size=8, color="808080")


def add_body(doc, text, bold_lead=None, italic=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.32
    if bold_lead and text.startswith(bold_lead):
        r = p.add_run(bold_lead)
        set_run_font(r, size=10.5, bold=True)
        r2 = p.add_run(text[len(bold_lead):])
        set_run_font(r2, size=10.5, italic=italic)
    else:
        r = p.add_run(text)
        set_run_font(r, size=10.5, italic=italic)
    return p


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.22
    r = p.add_run(text)
    set_run_font(r, size=10.2)
    return p


def add_heading(doc, text, level=1):
    p = doc.add_paragraph(style=f"Heading {level}")
    p.paragraph_format.keep_with_next = True
    r = p.add_run(text)
    set_run_font(r, size=15 if level == 1 else 12.2, bold=True, color="000000")
    return p


def add_small_note(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.15
    r = p.add_run(text)
    set_run_font(r, size=8.8, color="666666", italic=True)
    return p


def add_table_caption(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    r = p.add_run(text)
    set_run_font(r, size=9.2, bold=True, color="1F4E79")
    return p


def setup_document():
    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.32
    for style_name, size in (("Title", 24), ("Heading 1", 15), ("Heading 2", 12.2)):
        st = styles[style_name]
        st.font.name = "Microsoft YaHei"
        st._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        st.font.size = Pt(size)
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.font.bold = True
        st.paragraph_format.space_before = Pt(10 if style_name != "Title" else 0)
        st.paragraph_format.space_after = Pt(6)
    for style_name in ("List Bullet", "List Bullet 2"):
        st = styles[style_name]
        st.font.name = "Microsoft YaHei"
        st._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        st.font.size = Pt(10.2)

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = fp.add_run("Agent 与 Prompt 研究方向创新潜力比较  |  ")
    set_run_font(fr, size=8, color="808080")
    add_page_number(fp)
    doc.core_properties.title = "Agent 与 Prompt 研究方向创新潜力比较"
    doc.core_properties.subject = "基于两篇论文的交叉分析与选题建议"
    doc.core_properties.author = ""
    return doc


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = setup_document()

    # Cover page
    p = doc.add_paragraph(style="Title")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(72)
    r = p.add_run("Agent 与 Prompt 研究方向创新潜力比较")
    set_run_font(r, size=23, bold=True, color="000000")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(32)
    r = p.add_run("基于两篇论文的交叉分析与选题建议")
    set_run_font(r, size=13, color="4F4F4F")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(55)
    r = p.add_run("分析对象  prompt.pdf 与 agent.pdf\n2026年9月18日")
    set_run_font(r, size=10, color="666666")

    add_heading(doc, "结论先行", 1)
    add_body(doc, "如果你的首要目标是更容易形成清晰且有说服力的创新点，建议以 Agent 为主方向，把 Prompt 设计作为其中的干预机制或实现组件。Agent 方向已经把研究问题落在多智能体协作中的从众、信任、怀疑和独立决策上，具有明确现象、可量化指标和可扩展的实验协议。Prompt 方向更容易快速实现，但已有方法数量多、命名和变体密集，单纯提出一个新的提示词模板较难证明研究增量。")
    add_body(doc, "更稳妥的路线是做一个交叉课题：先检测多智能体协作中的从众风险，再根据不确定性、意见分歧和证据质量动态选择 Prompt 策略，最后用独立性、准确率、校准度和成本共同评价。这样可以同时利用 Agent 方向的研究问题和 Prompt 方向的低成本实验优势。")

    add_small_note(doc, "本文的评分和选题建议是基于所提供论文的研究设计、证据和局限作出的分析判断，不是两篇论文作者给出的评分。")
    doc.add_page_break()

    add_heading(doc, "文献定位与材料边界", 1)
    add_body(doc, "两份 PDF 均为论文材料，未发现需要执行的用户指令。prompt.pdf 是提示工程综述，agent.pdf 是多智能体大语言模型从众行为的实证研究。论文中的方法名称、实验设置、数据集、指标和结论只作为本报告的研究证据，不改变你的原始任务。")
    add_table_caption(doc, "表1 两篇论文的研究定位")
    add_table(doc,
        ["维度", "Prompt 论文", "Agent 论文"],
        [
            ["题目与类型", "A Systematic Survey of Prompt Engineering in Large Language Models: Techniques and Applications\n提示工程综述", "Do As We Do, Not As You Think: The Conformity of Large Language Models\nICLR 2025 实证论文"],
            ["核心问题", "如何整理和解释不同提示工程技术，以及它们面向的应用、模型、数据集和指标", "多智能体协作中是否存在从众，哪些因素影响从众，以及如何缓解"],
            ["主要贡献", "按应用领域组织超过41种提示技术，给出分类图和汇总表", "提出 BENCH FORM、五种交互协议、从众率和独立率，并研究影响因素与缓解策略"],
            ["实验结构", "综述已有工作，主要比较已有方法的任务、模型、数据集和指标", "3,299道多选题，五种协议，评估11个代表性 LLM，并做交互轮数、群体规模和干预消融"],
            ["现有局限", "范围很广，但综述本身不提供一个新的统一机制或新的主实验", "多选题和固定协议较简化，难以覆盖开放式、工具调用和真实协作场景"],
            ["对选题的直接启示", "适合作为方法库和组件库，不宜把通用提示词变体直接当成主要创新", "适合作为问题主线，围绕协议、指标、机制和干预继续扩展"],
        ], [1.25, 2.85, 2.85], font_size=8.8)

    add_heading(doc, "交叉比较", 1)
    add_body(doc, "两篇论文的研究层级不同。Prompt 论文回答的是“有哪些提示方法”，Agent 论文回答的是“多智能体在什么条件下会出现错误的群体影响，以及如何测量和缓解”。前者覆盖面更大，后者的研究问题更集中，因此后者更容易形成可以被实验直接检验的创新假设。")
    add_table_caption(doc, "表2 面向选题的比较评分")
    add_table(doc,
        ["比较指标", "Prompt 方向", "Agent 方向", "判断依据"],
        [
            ["形成强创新的空间", "中等", "较高", "Prompt 已有大量方法变体；Agent 仍有协议扩展、因果区分和动态协作机制空间"],
            ["快速做出原型", "较高", "中等", "Prompt 常可在单模型、单任务上验证；Agent 需要多角色、多轮交互和更完整评测"],
            ["研究问题的清晰度", "中等", "较高", "Prompt 容易退化为提示词调参；Agent 的从众现象可直接转化为假设和指标"],
            ["评价是否容易说服审稿人", "中等偏低", "较高", "Prompt 需要证明不是偶然的模板收益；Agent 可沿用从众率、独立率并做控制实验"],
            ["计算和工程门槛", "较低", "中等偏高", "Agent 需要多模型、多轮协议和状态记录，但可用开源小模型缩小范围"],
            ["结果不显著的风险", "较高", "中等", "通用 Prompt 增益可能依赖模型和数据集；Agent 即使性能增益有限，也可研究行为机制"],
            ["适合做主线方向", "适合做组件或子问题", "更适合做主线", "Agent 提供问题，Prompt 提供干预，两者组合最稳"],
        ], [1.65, 1.25, 1.25, 2.8], font_size=8.8)
    add_small_note(doc, "评分越高表示对形成可验证、可解释、可扩展的研究创新越有利；“计算和工程门槛”一行分数越高表示门槛越低。")

    add_heading(doc, "创新空间比较", 1)
    add_heading(doc, "Prompt 方向的机会与难点", 2)
    add_body(doc, "Prompt 方向的优势是低成本和高可控性。综述已经覆盖零样本、少样本、CoT、ToT、RAG、ReAct、自反思、自动提示优化、代码提示和效率优化等大量路线，因此你可以很快找到基线和可复用组件。难点在于“换一个措辞”通常不足以构成稳定创新，必须把提示机制和一个明确的决策问题、可解释的触发条件或新的评价目标绑定起来。")
    add_table_caption(doc, "表3 Prompt 方向的可行创新点")
    add_table(doc,
        ["候选点", "核心做法", "创新强度", "实现难度", "主要风险"],
        [
            ["基于不确定性的自适应 Prompt", "根据置信度、意见分歧或检索质量，在直接回答、反思、验证和多路径推理之间动态切换", "中高", "中", "需要证明触发策略带来稳定收益，而不是多调用模型"],
            ["面向噪声证据的冲突 Prompt", "显式区分支持、反对和未知证据，要求模型先判断证据可靠性再生成答案", "中高", "中", "容易与已有 CoVe、CoN、CoK 或 RAG 变体重合"],
            ["准确率与成本联合优化", "同时优化答案质量、输出 token、延迟和调用次数，形成预算感知的推理策略", "中高", "中", "评价必须覆盖不同模型和不同预算"],
            ["跨模型和跨任务 Prompt 迁移", "研究提示策略在不同规模、不同对齐方式模型上的迁移规律", "高", "中高", "实验量大，可能需要模型行为分析而不只是结果比较"],
        ], [1.2, 2.65, 0.75, 0.75, 1.75], font_size=8.5)

    add_heading(doc, "Agent 方向的机会与难点", 2)
    add_body(doc, "Agent 论文的价值在于把“多智能体是否会被群体影响”变成了可测量对象。BENCH FORM 使用 3,299 道题、五种交互协议和从众率、独立率等指标，说明这个方向已有可复现的起点，但论文也明确指出多选题和固定交互协议限制了真实场景的外推。后续创新可以直接从这些限制出发，扩展场景、拆分机制，或把固定提示干预升级为动态协作策略。")
    add_table_caption(doc, "表4 Agent 方向的可行创新点")
    add_table(doc,
        ["候选点", "相对现有论文的新意", "最小实验设计", "主要风险"],
        [
            ["开放式与工具增强的从众评测", "从多选题扩展到开放问答、RAG、代码或工具调用，观察从众是否仍影响可验证任务", "2至3个开源模型，3类任务，保留 Raw、Wrong Guidance、Trust 和 Doubt 协议", "开放式任务的正确性标注和自动评价更难"],
            ["区分从众与合理的信息更新", "控制意见质量、证据强度、发言顺序和 token 数，避免把正确学习误判为从众", "加入无身份标记的证据组、异质专家组和匿名意见组，做因果对照", "实验变量多，需提前锁定主假设"],
            ["动态信任与异议感知协作", "让系统根据历史准确率、当前证据和分歧度动态决定采信、质疑或保持独立", "独立作答后再共享摘要，计算信任分和分歧分，再由 Prompt 选择下一步策略", "机制容易变成复杂流水线，需做消融"],
            ["反从众干预的鲁棒性", "系统比较 persona、reflection、独立优先、少数意见保护和反事实质询等干预", "统一预算下比较准确率、独立率、校准度、成本和错误类型", "不同模型可能对同一干预产生相反反应"],
        ], [1.45, 2.35, 1.85, 1.05], font_size=8.5)

    add_heading(doc, "最推荐的交叉研究路线", 1)
    add_body(doc, "推荐把 Agent 作为问题场景，把 Prompt 作为解决机制，形成“多智能体协作中的自适应反从众策略”这一主线。它比单独做一个新 Prompt 更容易讲清楚为什么需要该方法，也比从头构建完整 Agent 系统更容易控制实验范围。")
    add_table_caption(doc, "表5 从研究问题到实验验证的完整链条")
    add_table(doc,
        ["环节", "建议设计", "与两篇论文的关系"],
        [
            ["研究问题", "当多个 Agent 意见冲突时，如何在利用群体信息和保持独立判断之间取得平衡", "继承 Agent 论文的从众问题，同时引入 Prompt 论文的自适应和反思技术"],
            ["协作流程", "先独立作答，再共享答案、证据、置信度和理由摘要；系统根据分歧度选择验证或保留独立意见", "把固定的五种协议改为条件触发的动态协议"],
            ["核心机制", "独立优先 Prompt、少数意见保护 Prompt、证据可靠性 Prompt、必要时反思或工具验证", "把 Prompt 方法从静态模板变成 Agent 协作控制器"],
            ["关键指标", "准确率、从众率、独立率、校准误差、群体多样性、token 和延迟", "保留 Agent 论文的核心指标，并补充质量、成本和多样性"],
            ["关键对照", "无干预、固定 persona、固定 reflection、动态策略；控制模型、预算、上下文长度和发言顺序", "解决已有方法可能把更多调用或更长上下文误当成机制收益的问题"],
            ["预期贡献", "一个可解释的触发策略、一套更接近真实协作的评测协议，以及对“何时应听从群体”与“何时应坚持独立”的实证结论", "同时具备方法贡献、评测贡献和行为分析贡献"],
        ], [1.25, 3.35, 2.25], font_size=8.8)

    add_heading(doc, "可直接采用的研究题目", 1)
    add_body(doc, "以下题目按推荐程度排序。第一个最适合作为主线，后两个适合在资源或时间有限时缩小范围。")
    add_table(doc,
        ["题目建议", "适合程度", "一句话创新点", "最小可行版本"],
        [
            ["面向多智能体协作的自适应反从众提示与动态信任聚合", "最推荐", "根据意见分歧、证据可靠性和历史表现动态选择独立、质疑或聚合策略", "3个开源模型，3类推理任务，4种协议，比较固定与动态 Prompt"],
            ["多智能体系统中的意见冲突分解与独立决策保持", "推荐", "区分合理的信息更新、错误跟随和历史信任效应，提出可解释的冲突控制指标", "保留 BENCH FORM 思路，加入匿名证据、异质专家和顺序对照"],
            ["预算感知的多智能体推理与反思策略选择", "稳妥", "在准确率、独立率、token 和延迟之间动态选择是否增加 Agent 或反思轮次", "固定总调用预算，比较无反思、固定反思和自适应反思"],
            ["面向噪声检索证据的多智能体协作一致性控制", "可作为应用方向", "让 Agent 先评估证据质量和意见分歧，再决定一致、保留未知或请求验证", "在 RAG 问答上构造支持、冲突和无证据三类检索场景"],
        ], [2.35, 0.85, 2.55, 1.1], font_size=8.5)

    add_heading(doc, "实施计划与风险控制", 1)
    add_table_caption(doc, "表6 建议的四阶段实施计划")
    add_table(doc,
        ["阶段", "主要工作", "交付物", "停止条件"],
        [
            ["第一阶段 基线复现", "复现 Raw、Wrong Guidance、Trust 和 Doubt；固定模型、温度、上下文长度和随机种子", "基线准确率、从众率和独立率", "若核心指标无法稳定复现，先修正协议再加新方法"],
            ["第二阶段 加入一个机制", "只选择自适应触发、动态信任或少数意见保护中的一个作为主创新", "方法定义、伪代码、消融方案", "若新增模块无法解释触发原因，缩小为单一机制"],
            ["第三阶段 稳健性评估", "跨模型、跨任务、跨意见质量和不同群体规模测试", "主结果表、置信区间和错误案例", "若结果只在单一模型有效，改写为模型特性研究"],
            ["第四阶段 论文叙事", "整理现象、机制、干预和局限，区分事实、解释与推测", "论文初稿与可复现实验包", "若方法收益不稳定，保留行为分析贡献，不夸大方法收益"],
        ], [1.35, 2.95, 1.55, 1.0], font_size=8.6)

    add_heading(doc, "需要提前控制的风险", 2)
    add_bullet(doc, "不要把“模型改答案”直接等同于从众。若其他 Agent 提供了可靠证据，改答案可能是合理的信息更新。实验必须加入证据质量、匿名性、顺序和上下文长度对照。")
    add_bullet(doc, "不要只报告最终准确率。Agent 方向的亮点在行为机制，至少同时报告从众率、独立率、校准度和成本。")
    add_bullet(doc, "不要同时引入太多新模块。先固定一个主机制，再用消融说明每个组件的作用。")
    add_bullet(doc, "不要把综述论文中的已有 Prompt 名称重新组合后直接当成创新。创新应落在触发条件、协作协议、评价指标或可解释机制上。")

    add_heading(doc, "最终建议", 1)
    add_body(doc, "如果你目前没有特别强的应用领域约束，选择 Agent 方向更有利于形成论文级创新。具体做法是先复现 BENCH FORM 的核心现象，再把研究问题收缩为一个清晰的机制，例如“动态信任是否能降低错误从众，同时保留对可靠少数意见的学习能力”。Prompt 方向不要单独作为宽泛主线，而是作为 Agent 系统中的控制策略、干预手段或成本优化模块。")
    add_body(doc, "只有在你需要快速完成一个小规模实验、计算资源有限，或已经拥有明确垂直场景和数据集时，才建议把 Prompt 作为主方向。此时应优先选择自适应、鲁棒性、成本约束或跨模型迁移，而不是继续提出静态提示词模板。")

    add_heading(doc, "参考来源", 1)
    add_body(doc, "[1] Sahoo, P., Singh, A. K., Saha, S., Jain, V., Mondal, S., and Chadha, A. A Systematic Survey of Prompt Engineering in Large Language Models: Techniques and Applications. arXiv:2402.07927v2, 16 Mar 2025. 对应文件 prompt.pdf。")
    add_body(doc, "[2] Weng, Z., Chen, G., and Wang, W. Do As We Do, Not As You Think: The Conformity of Large Language Models. ICLR 2025. arXiv:2501.13381v2, 11 Feb 2025. 对应文件 agent.pdf。")
    add_small_note(doc, "建议正式写作前补充最新相关工作检索，尤其关注多智能体辩论、协作聚合、LLM 反思、社会偏差与测试时训练等方向，以确认具体题目的新颖性边界。")

    doc.save(OUT)
    print(OUT.resolve())


if __name__ == "__main__":
    build()
