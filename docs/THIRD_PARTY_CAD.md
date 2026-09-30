# CAD 可选第三方组件

本仓库只声明公开上游依赖，不捆绑 CAD 二进制、商业 SDK、Bonsai/Blender 或用户 CAD 文件。

| 组件 | 固定版本 | 许可 | 官方来源 |
| --- | --- | --- | --- |
| ezdxf | 1.4.4 | MIT | https://ezdxf.readthedocs.io/en/stable/ · https://github.com/mozman/ezdxf |
| IfcOpenShell 核心 Python 组件 | 0.9.0 | LGPL-3.0-or-later | https://docs.ifcopenshell.org/ifcopenshell-python/installation.html · https://github.com/IfcOpenShell/IfcOpenShell/tree/v0.9.0 |

验证环境使用 PyPI 官方发布的 Python 3.12 / macOS ARM64 wheel。IfcOpenShell 的其它应用目录可能有不同许可；本功能不引入 Bonsai。项目通过公开 Python API 使用可替换的、独立安装的库，不修改库源码，不限制用户替换接口兼容版本。后续若分发库/wheel或完整应用包，应保留相应版权、许可、源码获取与重新链接/替换权利，按实际分发方式履行上游要求。

许可证原文：

- ezdxf：https://github.com/mozman/ezdxf/blob/v1.4.4/LICENSE
- IfcOpenShell LGPL：https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.9.0/COPYING.LESSER
- LGPL 引用的 GPL：https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.9.0/COPYING

DWG 当前未启用。ODA Viewer/File Converter 对非会员仅许可非商业用途，不能当免费商用依赖。GNU LibreDWG 是 GPLv3+，版本/实体/元数据往返及分发条件尚未在本项目验证，故没有下载、构建、捆绑或调用。未来适配应单独审阅许可、固定来源和安全进程边界；不能把独立 CLI 误解为自动免除许可义务。
