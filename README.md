# skilled-epub

用于处理电子书格式转换的 skill，核心能力包括扫描版 PDF 转为 EPUB、MOBI 转为 EPUB。

## 功能特性
- MOBI 转 EPUB：将 MOBI 电子书转换为 EPUB 格式。
- `build` 会从已检查的 Markdown 构建 EPUB，并自动生成 `router.json` 导航映射。
- 仓库核心以 `skills/skilled-epub/` 形式组织，可被 open-claw 等支持 skills 的运行时使用。

## 命令行用法

skill 内部使用本地脚本执行确定性操作。直接调试时可先设置脚本路径：

### `screenshot`

将 PDF 指定页面截图到 `output/book/images/`，已存在的图片会跳过。

```bash
screenshot --start 5          # 只截图第 5 页
screenshot --end 10           # 截图第 1-10 页
screenshot --start 5 --end 10 # 截图第 5-10 页
screenshot --all              # 截图全部页面
```


### `ocr`

将页面图片识别为 `output/book/md/page_XXXX.md`，页码范围参数与 `screenshot` 一致。缺少截图时会自动先截图。

```bash
ocr --start 5
ocr --end 10
ocr --start 5 --end 10
ocr --all
```



### `build`

从 `output/book/md/page_*.md` 构建 EPUB，并自动生成/更新 `router.json` 导航映射。

```bash
build
```



## License

MIT，详见 [LICENSE](LICENSE)。
