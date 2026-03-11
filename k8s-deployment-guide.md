# Kubernetes Deployment Guide for Aether AI Agent

本文档提供将Aether AI Agent部署到Kubernetes集群的详细指南。

## 快速部署

### 1. 前提条件

- Kubernetes集群（v1.19+）
- kubectl命令行工具
- 访问镜像仓库的权限
- 确保已构建并推送Docker镜像（使用build.sh脚本）

### 2. 部署步骤

#### 步骤1：构建和推送Docker镜像

```bash
# 使用build.sh脚本构建Docker镜像
./build.sh -r docker.io -p your-username/aether -t v1.0.0

# 如果需要推送镜像
./build.sh -r docker.io -p your-username/aether -t v1.0.0 --no-build-only
```

#### 步骤2：更新deployment.yaml中的镜像名称

编辑[deployment.yaml](/Users/tigerweili/github_tiger/06-Aether/deployment.yaml)文件，将镜像名称更新为您实际构建的镜像：

```yaml
# 在第21行附近
image: docker.io/your-username/aether:v1.0.0
```

#### 步骤3：部署到Kubernetes

```bash
# 创建命名空间（可选）
kubectl create namespace aether

# 应用部署配置
kubectl apply -f deployment.yaml

# 查看部署状态
kubectl get deployments -n default
kubectl get pods -n default
kubectl get services -n default
```

#### 步骤4：验证部署

```bash
# 查看Pod日志
kubectl logs -l app=aether -n default

# 查看服务详情
kubectl describe service aether-service -n default

# 端口转发到本地测试（可选）
kubectl port-forward svc/aether-service 8001:8001 -n default

# 然后在浏览器中访问 http://localhost:8001
```

## 环境变量配置

### 核心环境变量

以下环境变量需要根据您的环境进行配置：

| 变量名 | 描述 | 默认值 | 必需 |
|--------|------|--------|------|
| `OLLAMA_API_BASE_HOST_PORT` | Ollama API地址 | `http://localhost:11434` | 是 |
| `SLM_MODEL_NAME` | 默认AI模型 | `qwen2.5:7b` | 是 |
| `AETHER_HOME` | Aether配置文件目录 | `/root/.nanobot` | 是 |
| `EMBEDDING_MODEL_NAME` | 嵌入模型名称 | `bge-small-en-v1.5` | 是 |
| `ROCKETMQ_MCP_SERVER_URL` | RocketMQ MCP服务地址 | `http://rocketmq-mcp-server:8080` | 否 |
| `RERANK_MODEL_NAME` | 重排序模型名称 | `bge-reranker-v2-m3` | 否 |

### 配置方法

有两种方式配置环境变量：

#### 方法1：直接修改deployment.yaml

在`deployment.yaml`文件的`env`部分直接修改变量值。

#### 方法2：使用ConfigMap

创建独立的ConfigMap，然后在Deployment中引用：

```yaml
# 创建ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: aether-env-config
  namespace: default
data:
  OLLAMA_API_BASE_HOST_PORT: "http://ollama-service:11434"
  SLM_MODEL_NAME: "qwen2.5:14b"
  # 其他变量...

# 然后在Deployment中引用
envFrom:
- configMapRef:
    name: aether-env-config
```

## 持久化存储

当前配置使用`emptyDir`作为临时存储。如果需要持久化存储，建议使用PersistentVolumeClaim（PVC）。

### 示例PVC配置

```yaml
# 在deployment.yaml中添加
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: aether-pvc
  namespace: default
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: "standard"

# 然后在Deployment中修改volume配置
volumes:
- name: config-volume
  persistentVolumeClaim:
    claimName: aether-pvc
```

## 高级配置

### 1. 自定义镜像标签

使用build.sh脚本生成带时间戳的镜像标签：

```bash
# 生成时间戳格式的标签
./build.sh -t ""  # 不指定tag，自动使用时间戳格式
```

然后在deployment.yaml中使用该镜像：
```yaml
image: docker.io/youlixishi/aether:123-456-789
```

### 2. 水平扩展（Horizontal Pod Autoscaling）

创建HPA配置自动扩展Pod数量：

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: aether-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: aether-deployment
  minReplicas: 1
  maxReplicas: 5
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

### 3. 网络策略

如果需要限制网络访问，可以创建NetworkPolicy：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: aether-network-policy
  namespace: default
spec:
  podSelector:
    matchLabels:
      app: aether
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: aether
    ports:
    - protocol: TCP
      port: 8001
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: ollama
    ports:
    - protocol: TCP
      port: 11434
```

## 故障排除

### 常见问题

1. **Pod无法启动**
   - 检查镜像是否存在：`kubectl describe pod <pod-name>`
   - 检查资源限制是否足够
   - 检查环境变量配置

2. **服务无法访问**
   - 检查Service selector是否正确匹配Pod标签
   - 检查防火墙规则
   - 使用`kubectl port-forward`测试内部访问

3. **配置问题**
   - 确保所有必需环境变量已设置
   - 检查ConfigMap是否正确挂载

### 调试命令

```bash
# 查看Pod详细状态
kubectl describe pod -l app=aether

# 查看Pod日志
kubectl logs -f -l app=aether

# 进入Pod调试
kubectl exec -it <pod-name> -- /bin/sh

# 查看服务端点
kubectl get endpoints aether-service

# 查看事件
kubectl get events --sort-by=.metadata.creationTimestamp
```

## 清理资源

```bash
# 删除所有相关资源
kubectl delete -f deployment.yaml

# 或者单独删除
kubectl delete deployment aether-deployment
kubectl delete service aether-service
kubectl delete configmap aether-config
```

## 参考链接

- [Aether项目GitHub](https://github.com/tigerweili/06-Aether)
- [Kubernetes官方文档](https://kubernetes.io/docs/)
- [Docker镜像构建脚本](build.sh)
- [Dockerfile配置](Dockerfile)