---
name: "kubernetes-operations"
description: "Kubernetes集群管理和故障排查命令。当用户需要执行k8s集群操作、查看资源状态或排查问题时调用。"
---

# Kubernetes操作命令技能

此技能提供Kubernetes集群的常用操作命令和故障排查指南。

## 概述

Kubernetes操作命令技能包含集群管理、资源操作、故障排查等常用命令，帮助用户快速执行k8s相关操作。

## 基础命令

### 集群信息查看

```bash
# 查看集群信息
kubectl cluster-info

# 查看节点状态
kubectl get nodes
kubectl describe node <node-name>

# 查看集群组件状态
kubectl get componentstatuses
```

### 命名空间操作

```bash
# 查看所有命名空间
kubectl get namespaces

# 切换到指定命名空间
kubectl config set-context --current --namespace=<namespace>

# 查看当前命名空间
kubectl config view --minify | grep namespace
```

## 资源管理

### Pod操作

```bash
# 查看所有Pod
kubectl get pods --all-namespaces
kubectl get pods -n <namespace>

# 查看Pod详细信息
kubectl describe pod <pod-name> -n <namespace>

# 查看Pod日志
kubectl logs <pod-name> -n <namespace>
kubectl logs -f <pod-name> -n <namespace>  # 实时查看日志

# 进入Pod容器
kubectl exec -it <pod-name> -n <namespace> -- /bin/bash

# 删除Pod
kubectl delete pod <pod-name> -n <namespace>
```

### Deployment操作

```bash
# 查看Deployment
kubectl get deployments -n <namespace>

# 查看Deployment详细信息
kubectl describe deployment <deployment-name> -n <namespace>

# 扩缩容
kubectl scale deployment <deployment-name> --replicas=3 -n <namespace>

# 更新镜像
kubectl set image deployment/<deployment-name> <container-name>=<new-image> -n <namespace>

# 回滚Deployment
kubectl rollout undo deployment/<deployment-name> -n <namespace>
```

### Service操作

```bash
# 查看Service
kubectl get services -n <namespace>

# 查看Service详细信息
kubectl describe service <service-name> -n <namespace>

# 端口转发到本地
kubectl port-forward service/<service-name> 8080:80 -n <namespace>
```

## 配置管理

### ConfigMap和Secret

```bash
# 查看ConfigMap
kubectl get configmaps -n <namespace>

# 查看Secret
kubectl get secrets -n <namespace>

# 创建ConfigMap
kubectl create configmap <configmap-name> --from-file=<file-path> -n <namespace>

# 创建Secret
kubectl create secret generic <secret-name> --from-literal=key=value -n <namespace>
```

### 资源文件操作

```bash
# 应用YAML文件
kubectl apply -f <file.yaml>

# 删除YAML文件定义的资源
kubectl delete -f <file.yaml>

# 查看资源定义
kubectl get <resource-type> <resource-name> -o yaml -n <namespace>
```

## 故障排查

### 网络问题排查

```bash
# 检查网络连通性
kubectl run test-pod --image=busybox --rm -it --restart=Never -- nslookup <service-name>

# 检查DNS解析
kubectl run dns-test --image=busybox --rm -it --restart=Never -- nslookup kubernetes.default

# 检查Service端点
kubectl get endpoints <service-name> -n <namespace>
```

### 存储问题排查

```bash
# 查看PersistentVolume
kubectl get pv

# 查看PersistentVolumeClaim
kubectl get pvc -n <namespace>

# 查看StorageClass
kubectl get storageclass
```

### 资源使用情况

```bash
# 查看节点资源使用
kubectl top nodes

# 查看Pod资源使用
kubectl top pods -n <namespace>

# 查看资源限制和请求
kubectl describe node <node-name> | grep -A 10 "Allocated resources"
```

## 高级操作

### 标签和选择器

```bash
# 给资源添加标签
kubectl label pods <pod-name> environment=production -n <namespace>

# 根据标签选择资源
kubectl get pods -l environment=production -n <namespace>

# 删除标签
kubectl label pods <pod-name> environment- -n <namespace>
```

### 事件查看

```bash
# 查看所有事件
kubectl get events --all-namespaces

# 查看特定命名空间事件
kubectl get events -n <namespace>

# 按时间排序查看事件
kubectl get events --sort-by=.metadata.creationTimestamp -n <namespace>
```

### 调试工具

```bash
# 创建调试Pod
kubectl run debug-pod --image=busybox --rm -it --restart=Never -- /bin/sh

# 检查API服务器连通性
kubectl get --raw /healthz

# 检查集群组件健康状态
kubectl get --raw /readyz
```

## 常用场景

### 应用部署

```bash
# 部署应用
kubectl apply -f deployment.yaml

# 验证部署
kubectl get pods -l app=my-app

# 检查服务
kubectl get service my-app-service
```

### 故障恢复

```bash
# 重启Deployment
kubectl rollout restart deployment/<deployment-name>

# 查看部署历史
kubectl rollout history deployment/<deployment-name>

# 回滚到特定版本
kubectl rollout undo deployment/<deployment-name> --to-revision=2
```

### 监控和日志

```bash
# 设置日志级别
kubectl logs <pod-name> --previous  # 查看之前容器的日志

# 监控资源变化
kubectl get pods -w  # 监控Pod变化

# 导出资源配置
kubectl get deployment <deployment-name> -o yaml > backup.yaml
```

## 安全最佳实践

### 权限管理

```bash
# 查看当前用户权限
kubectl auth can-i list pods

# 查看ServiceAccount权限
kubectl auth can-i --list --as=system:serviceaccount:<namespace>:<serviceaccount>
```

### 安全配置

```bash
# 查看Pod安全上下文
kubectl get pod <pod-name> -o jsonpath='{.spec.securityContext}'

# 检查网络策略
kubectl get networkpolicies -n <namespace>
```

## 注意事项

1. **生产环境谨慎操作**：删除操作前确认资源名称和命名空间
2. **备份重要配置**：修改前导出当前配置
3. **权限最小化**：使用适当的RBAC权限
4. **监控操作影响**：重要操作期间监控集群状态
5. **版本兼容性**：注意kubectl版本与集群版本的兼容性

## 文件位置

- 此技能文件：`skills/Kubernetes操作命令.md`
- 相关工具：kubectl命令行工具
- 配置文件：kubeconfig文件