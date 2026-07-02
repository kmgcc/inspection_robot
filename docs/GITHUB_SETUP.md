# GitHub 私有仓库配置

## 1. 在 GitHub 创建私有仓库

仓库名建议：

```text
inspection-robot
```

可见性选择 `Private`。

## 2. 本地初始化

在本目录执行：

```bash
git init
git add .
git commit -m "Initial inspection robot dashboard"
```

## 3. 关联远程仓库

把下面地址换成你们自己的私有仓库地址：

```bash
git remote add origin git@github.com:YOUR_ORG_OR_USER/inspection-robot.git
git branch -M main
git push -u origin main
```

如果使用 HTTPS：

```bash
git remote add origin https://github.com/YOUR_ORG_OR_USER/inspection-robot.git
git branch -M main
git push -u origin main
```

## 4. 队友接手

```bash
git clone git@github.com:YOUR_ORG_OR_USER/inspection-robot.git
cd inspection-robot
python3 -m pip install -r requirements.txt
scripts/run_local.sh
```

再按 `docs/DEPLOY_TO_CAR.md` 部署到小车。
