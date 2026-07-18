# 附录 B. Git 常用命令与恢复演练

## B.1 查看是否有未保存修改

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>git status --short<br />
git diff<br />
git diff --cached</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## B.2 保存一次工作

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>git add -A<br />
git commit -m "feat: describe the change"<br />
git push</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## B.3 创建完整代码备份

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>mkdir backups\code<br />
git bundle create backups\code\douyin-recorder-all.bundle --all<br />
git bundle verify backups\code\douyin-recorder-all.bundle<br />
certutil -hashfile backups\code\douyin-recorder-all.bundle SHA256</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## B.4 从 Bundle 恢复

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>git clone douyin-recorder-all.bundle douyin-recorder-restored<br />
cd douyin-recorder-restored<br />
git branch -a<br />
git tag<br />
verify.bat</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## B.5 数据恢复演练

> **1.** 在隔离目录或测试机器停止应用。
>
> **2.** 恢复 config/、userdata/ 和与数据库匹配的 records/。
>
> **3.** 恢复已验证代码 tag 或从 Git Bundle 克隆。
>
> **4.** 运行 verify.bat，启动后核对房间、场次、时间线、媒体、jobs 和审计。
>
> **5.** 记录恢复耗时、缺失项和校验结果；修订备份脚本。

