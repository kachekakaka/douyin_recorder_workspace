# 14. Windows / Docker 可执行部署

## 14.1 Windows 双击运行目标

正式 Windows 发布采用参考仓库的便携运行包思路：仓库或 Release 中保存校验过的 Python runtime pack、media runtime pack 和 manifest；start.bat 安全解压到被 Git 忽略的 .runtime/ 与 vendor 运行目录，随后启动 python -m app。运行包不得包含实际配置、Cookie、数据库或媒体。

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>双击 start.bat<br />
1. 校验 runtime-manifest.json 和 SHA-256<br />
2. 缺失时解压 portable Python / 锁定依赖 / FFmpeg<br />
3. 从 *.default 创建或补全实际配置<br />
4. 执行快速 readiness 检查<br />
5. 启动 python -m app<br />
6. 打开 http://127.0.0.1:&lt;port&gt;/</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

update.bat 只做 fast-forward 更新并调用 verify.bat；实际 config/、userdata/、records/ 和 .runtime/ 均由 .gitignore 排除，不会被 git pull 覆盖。

## 14.2 Docker Compose

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>services:<br />
app:<br />
image: ghcr.io/&lt;owner&gt;/douyin-recorder:&lt;version&gt;<br />
restart: unless-stopped<br />
ports:<br />
- "3399:3399"<br />
volumes:<br />
- ./config:/data/config<br />
- ./userdata:/data/userdata<br />
- /path/to/records:/records<br />
read_only: true<br />
tmpfs:<br />
- /tmp:rw,noexec,nosuid,size=256m<br />
cap_drop: [ALL]<br />
security_opt:<br />
- no-new-privileges:true</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

- 固定三个持久化映射：CONFIG_DIR、USERDATA_DIR、RECORDS_DIR；重建镜像不得改变。

- 容器非 root、只读根文件系统、丢弃 capabilities、健康检查、日志轮转和优雅停止。

- 数据库和 userdata 优先本地卷；records 可映射 NAS。

- 公网仅通过 HTTPS 反向代理访问，不直接暴露应用端口。

## 14.3 回滚

> **1.** 停止应用并备份当前 config、userdata 和 records 索引/必要媒体。
>
> **2.** git fetch --tags；切换到已验证 tag 或 commit。
>
> **3.** 恢复与该代码版本匹配的 SQLite 备份；不能只回退代码而忽略迁移。
>
> **4.** 运行 verify.bat 或容器自检，核对房间、场次、时间线、任务和媒体。

