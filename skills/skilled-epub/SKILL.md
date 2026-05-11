# skilled-epub

扫描版 PDF 转 EPUB 的两阶段工作流：先批量截图发给视觉模型 OCR 输出 Markdown 中间产物供人工审核，再打包为 EPUB。

支持**逐步处理**（默认每次5页），不需要一次处理完整本书。

OCR 默认通过 Zode 中转调用 Kimi `kimi-k2.5`，Zode Key 从项目根目录 `./output/.env` 读取。

## 用法

```bash
# 先配置 Zode Key（二选一，推荐 key，与现有 ./output/.env 兼容）
echo 'key=zode_xxx' > ./output/.env

# 逐步 OCR：每次处理5页，可分批执行
python skills/skilled-epub/scan_pdf_to_epub.py ocr <input.pdf> [--chunk-size 5] [--output-dir ./output] [--model kimi-k2.5]

# 指定范围处理（第21-50页）
python skills/skilled-epub/scan_pdf_to_epub.py ocr <input.pdf> --start 21 --end 50

# 阶段二：Markdown → EPUB
python skills/skilled-epub/scan_pdf_to_epub.py build <output_dir> [--title "书名"] [--author "作者"]
```

## 工作流

1. `ocr` 命令：按需渲染页面截图，发给视觉模型识别文字，每批输出一个 `chunk_XX.md`
2. 人工检查并编辑 `output_dir/*.md` 文件
3. `build` 命令：将所有 `chunk_*.md` 按顺序合并打包为 EPUB

## OCR 模式

### 内置 Kimi OCR（默认）

在 `./output/.env` 中配置任一变量即可：

```bash
key=zode_xxx
# 或
ZODE_KEY=zode_xxx
```

脚本会请求：

```text
POST https://zode.qa.qima-inc.com/api/proxy/forward/chat/completions
model: kimi-k2.5
```

可通过 `--model` 参数或 `OCR_MODEL` 环境变量指定模型，默认 `kimi-k2.5`。

### 内置 Claude API（后备）

设置 `ANTHROPIC_API_KEY` 环境变量：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python skills/skilled-epub/scan_pdf_to_epub.py ocr book.pdf
```

只有未配置 Zode Key 时才会回退到 Claude。

### 外部回调（open-claw 集成）

```python
from scan_pdf_to_epub import register_ocr_callback

def my_ocr(image_paths: list[str]) -> str:
    """接收页面截图路径列表，返回识别的 Markdown 文本。"""
    ...

register_ocr_callback(my_ocr)
```

## 输出目录结构

```
output_dir/
  chunk_00.md          # 第1-5页 OCR 结果
  chunk_01.md          # 第6-10页 OCR 结果
  images/
    page_0001.png      # 按需渲染的截图
    ...
  cover.png
```
