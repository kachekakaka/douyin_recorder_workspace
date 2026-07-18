# 抖音团播多直播间录播系统架构基线 v2.0

状态：**批准实施，附开工前修订项**  
日期：2026-07-18  
技术边界：抖音单平台、Python 单机后端、FastAPI、SQLite WAL、FFmpeg、静态 HTML/CSS/原生 JavaScript。

为便于 GitHub 阅读、逐章审查和后续 PR diff，本基线按章节拆分保存；下列文件按顺序拼接即为完整 v2.0 文本，内容来源于冻结版 DOCX/PDF 同一基线。

1. [封面与版本信息](architecture-baseline-v2.0/00-封面与版本信息.md)
2. [文档说明](architecture-baseline-v2.0/01-文档说明.md)
3. [目录](architecture-baseline-v2.0/02-目录.md)
4. [执行摘要](architecture-baseline-v2.0/03-执行摘要.md)
5. [1. 项目目标、范围与简化边界](architecture-baseline-v2.0/04-1-项目目标-范围与简化边界.md)
6. [2. 最终技术决策](architecture-baseline-v2.0/05-2-最终技术决策.md)
7. [3. Python 单机总体架构](architecture-baseline-v2.0/06-3-Python-单机总体架构.md)
8. [4. 多直播间运行模型](architecture-baseline-v2.0/07-4-多直播间运行模型.md)
9. [5. 端到端业务流程](architecture-baseline-v2.0/08-5-端到端业务流程.md)
10. [6. 推荐收礼人状态机](architecture-baseline-v2.0/09-6-推荐收礼人状态机.md)
11. [7. 媒体录制、时间轴与切片](architecture-baseline-v2.0/10-7-媒体录制-时间轴与切片.md)
12. [8. SQLite 数据模型与文件边界](architecture-baseline-v2.0/11-8-SQLite-数据模型与文件边界.md)
13. [9. 管理 API 与纯网页前端](architecture-baseline-v2.0/12-9-管理-API-与纯网页前端.md)
14. [10. 可靠性、恢复与资源保护](architecture-baseline-v2.0/13-10-可靠性-恢复与资源保护.md)
15. [11. 安全与凭据边界](architecture-baseline-v2.0/14-11-安全与凭据边界.md)
16. [12. 推荐源码仓库结构](architecture-baseline-v2.0/15-12-推荐源码仓库结构.md)
17. [13. GitHub 使用、发布与防丢方案](architecture-baseline-v2.0/16-13-GitHub-使用-发布与防丢方案.md)
18. [14. Windows / Docker 可执行部署](architecture-baseline-v2.0/17-14-Windows-Docker-可执行部署.md)
19. [15. 实施阶段、交付物与退出条件](architecture-baseline-v2.0/18-15-实施阶段-交付物与退出条件.md)
20. [16. 验收标准](architecture-baseline-v2.0/19-16-验收标准.md)
21. [17. 风险与应对](architecture-baseline-v2.0/20-17-风险与应对.md)
22. [附录 A. 配置与目录样例](architecture-baseline-v2.0/21-附录-A-配置与目录样例.md)
23. [附录 B. Git 常用命令与恢复演练](architecture-baseline-v2.0/22-附录-B-Git-常用命令与恢复演练.md)
24. [附录 C. 参考仓库与资料](architecture-baseline-v2.0/23-附录-C-参考仓库与资料.md)

## 不可改变的业务口径

系统只使用 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新当前推荐收礼人；首事件前、空 recipient、IM 断线及重连后未收到新事件时均为 `Unknown`。不得加入 OCR、人脸、声纹、礼物、弹幕、连麦成员或画面位置等 fallback；推荐对象切换不得重启 FFmpeg。
