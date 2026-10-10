# OpenIntelligentUI 来源与改编说明

来源：[CopilotKit/OpenIntelligentUI](https://github.com/CopilotKit/OpenIntelligentUI)。固定版本：`f6e4388b26a64b9a0714943b08a1ce622b924eec`。

本地 `frontend-src/src/components/open-intelligent-ui/` 改编自上游 `apps/app/src/components/generative-ui/open-generative-ui/` 的 `renderer.tsx`、`frame-content.ts`、`websandbox-loader.ts` 和 `schema.ts`，保留完成态协议、JetBrains Websandbox 加载、沙箱生命周期和连续高度测量。运行依赖 `@jetbrains/websandbox` 1.2.1，其许可证随该包保留。

改编只渲染完成态报告；未完成或错误时保留现有文字。移除聊天工具、导出、流式预览、外部资源与上游设计系统。宿主不暴露函数；限制网络、弹窗、表单和顶层页面导航，增加按序执行、取消与超时处理。主题仅由宿主显式传入样式；同一内容重复读取不会重复执行。高度上限 20,000 像素，超出部分通过报告内部滚动阅读。

以下完整保留上游 MIT 许可证：

```text
The MIT License

Copyright (c) Atai Barkai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
