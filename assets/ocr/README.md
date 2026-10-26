# 对象文字识别模型

文件：ch_PP-OCRv4_rec_mobile.onnx（10,857,958字节）。
SHA-256：48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b。

来源：RapidAI/RapidOCR 官方 v3.9.2 模型清单中的 PP-OCRv4 中文移动端识别模型：
https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile.onnx

模型源自 PaddlePaddle/PaddleOCR，由 RapidAI 提供 ONNX 转换。模型及其上游代码采用 Apache-2.0，随附 LICENSE-RapidOCR.txt。

本项目只使用识别模型，定位继续使用现有面板模板。预处理和 CTC 解码按照 PaddleOCR/RapidOCR 的公开输入输出约定实现：48像素高、BGR、归一化至[-1,1]、重复/空白折叠和模型内字典。实现参考 https://github.com/RapidAI/RapidOCR 。不依赖 RapidOCR Python 包，也不使用文字检测模型。模型随安装包提供，运行中没有下载和图片上传。
