# skilled-epub

扫描版 PDF 转 EPUB 的两阶段工作流：先批量截图发给视觉模型 OCR 输出 Markdown 中间产物供人工审核，再打包为 EPUB。

## 用法

```bash
# 阶段一：PDF → Markdown（默认每批5页，使用 Claude API OCR）
python skills/skilled-epub/scan_pdf_to_epub.py ocr <input.pdf> [--chunk-size 5] [--output-dir ./output] [--model claude-sonnet-4-20250514]

# 阶段二：Markdown → EPUB（人工审核 md 后执行）
python skills/skilled-epub/scan_pdf_to_epub.py build <output_dir> [--title "书名"] [--author "作者"]
```

## 工作流

1. `ocr` 命令：将 PDF 每页渲染为 PNG 截图，按批次（默认5页）发给视觉模型识别文字，每批输出一个 `chunk_XX.md`
2. 人工检查并编辑 `output_dir/*.md` 文件
3. `build` 命令：将所有 `chunk_*.md` 按顺序合并打包为 EPUB

## OCR 模式

### 内置 Claude API（推荐）

设置 `ANTHROPIC_API_KEY` 环境变量即可自动使用：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python skills/skilled-epub/scan_pdf_to_epub.py ocr book.pdf
```

可通过 `--model` 参数或 `OCR_MODEL` 环境变量指定模型。

### 外部回调（open-claw 集成）

```python
from scan_pdf_to_epub import register_ocr_callback

def my_ocr(image_paths: list[str]) -> str:
    """接收页面截图路径列表，返回识别的 Markdown 文本。"""
    # 调用 open-claw 多模态模型
    ...

register_ocr_callback(my_ocr)
```

未注册回调且未设置 API Key 时，仅保存页面截图，提示用户手动处理。

## 输出目录结构

```
output_dir/
  chunk_00.md          # 第1-5页 OCR 结果（可人工编辑）
  chunk_01.md          # 第6-10页 OCR 结果
  images/
    page_0001.png      # 第1页截图
    page_0002.png      # 第2页截图
    ...
  cover.png            # 封面图
```