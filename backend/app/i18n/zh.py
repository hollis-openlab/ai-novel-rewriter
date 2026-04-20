from __future__ import annotations

ZH_MESSAGES: dict[str, str] = {
    # Stage labels
    "stage.analyze": "分析",
    "stage.mark": "标记",
    "stage.rewrite": "改写",
    "stage.import": "导入",
    "stage.split": "拆分",
    "stage.assemble": "组装",

    # Stage errors
    "error.upstream_required": "请先完成「{upstream_label}」阶段",

    # Review feedback
    "review.feedback_prefix": "[审核反馈]",
    "review.fix_boundary": "[修复边界]",

    # Scene type defaults
    "scene.manual_mark": "手动标记",

    # Export labels
    "export.chapter_heading": "第 {idx} 章",
    "export.original": "原文",
    "export.rewritten": "改写",
    "export.toc": "目录",

    # Error messages
    "error.validation_failed": "请求校验失败",
    "error.not_found": "资源未找到",
    "error.internal": "服务器内部错误",
    "error.http": "HTTP 错误",
}
