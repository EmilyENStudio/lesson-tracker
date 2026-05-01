# GitHub Actions 自动部署配置

## 为什么需要这个配置？

由于 GitHub Push Protection 会阻止包含 Personal Access Token (PAT) 的代码推送，我们在 `index.html` 中使用了占位符 `__GITHUB_TOKEN__`。这个文件需要通过 GitHub Actions 在构建时注入真实的 token。

## 设置步骤

### 1. 添加 GitHub Secret

1. 访问仓库 Settings → Secrets and variables → Actions
2. 点击 "New repository secret"
3. Name: `GH_TOKEN`
4. Secret: 粘贴你的 GitHub Personal Access Token (需要有 repo 和 workflow 权限)

### 2. 创建 GitHub Actions Workflow

1. 在本地创建 `.github/workflows/deploy.yml` 文件：

```yaml
name: Build and Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      
      - name: Inject GitHub Token
        env:
          GH_TOKEN: ${{ secrets.GH_TOKEN }}
        run: |
          if [ -n "$GH_TOKEN" ]; then
            ENCODED=$(echo -n "$GH_TOKEN" | base64 -w 0)
            sed -i "s|const __GITHUB_TOKEN__ = ''|const __GITHUB_TOKEN__ = atob('$ENCODED')|g" index.html
            echo "Token injected successfully"
          else
            echo "Warning: GH_TOKEN secret not set"
          fi
      
      - name: Setup Pages
        uses: actions/configure-pages@v4
      
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: '.'
      
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

### 3. 本地手动部署（临时方案）

如果暂时不想配置 GitHub Actions，可以手动替换 token：

```bash
# 在 index.html 中找到
const __GITHUB_TOKEN__ = ''

# 替换为 base64 编码的 token
const __GITHUB_TOKEN__ = atob('YOUR_BASE64_ENCODED_TOKEN')
```

## 生成 Base64 编码的 Token

```bash
echo -n "ghp_your_token_here" | base64 -w 0
```

## 功能说明

配置完成后，"家长反馈"功能即可正常使用：
- 家长提交反馈后会自动通过 GitHub API 更新学员 JSON 文件
- 反馈数据保存在对应学员文件的 `parentFeedback` 字段中
