"""Git 仓库管理器。

负责解析 source_repos.yaml 配置文件，执行 git clone/pull 操作。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

# 默认配置文件名
_CONFIG_FILENAME = "source_repos.yaml"

# 默认超时（秒）
_DEFAULT_TIMEOUT = 300


@dataclass
class RepoConfig:
    """单个仓库的配置。"""

    domain_name: str
    repo_url: str
    branch: str = "main"
    sub_directory: Optional[str] = None


@dataclass
class GitResult:
    """Git 操作结果。"""

    domain: str
    success: bool
    action: str = ""  # "clone", "pull", "skip"
    commit_hash: Optional[str] = None
    error: Optional[str] = None
    message: str = ""


class GitManager:
    """Git 仓库管理器。

    负责：
    - 解析 ``workspace/src/source_repos.yaml`` 配置
    - 执行 git clone（新领域）/ git pull（已有领域）
    - 支持 HTTPS/SSH 协议
    - 超时控制和错误处理
    """

    def __init__(self, src_dir: Path, timeout: int = _DEFAULT_TIMEOUT):
        """初始化 Git 管理器。

        Args:
            src_dir: 源代码根目录，如 ``workspace/src/``
            timeout: Git 操作超时时间（秒）
        """
        self._src_dir = src_dir
        self._timeout = timeout
        self._config_file = src_dir / _CONFIG_FILENAME

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def load_config(self) -> list[RepoConfig]:
        """加载 source_repos.yaml 配置。

        Returns:
            RepoConfig 列表（配置不存在或格式错误时返回空列表）
        """
        if not self._config_file.exists():
            logger.info(f"[GitManager] 配置文件不存在，跳过 Git 拉取: {self._config_file}")
            return []

        try:
            import yaml
            with open(self._config_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                logger.warning(f"[GitManager] 配置文件为空或格式不正确")
                return []

            repos_raw = data.get("repos", [])
            if not isinstance(repos_raw, list):
                logger.warning("[GitManager] 配置文件中 'repos' 字段不是列表")
                return []

            configs: list[RepoConfig] = []
            for item in repos_raw:
                if not isinstance(item, dict):
                    continue
                domain_name = item.get("domain_name")
                repo_url = item.get("repo_url")
                if not domain_name or not repo_url:
                    logger.warning(f"[GitManager] 跳过不完整的配置项: {item}")
                    continue
                configs.append(RepoConfig(
                    domain_name=domain_name,
                    repo_url=repo_url,
                    branch=item.get("branch", "main"),
                    sub_directory=item.get("sub_directory"),
                ))

            logger.info(f"[GitManager] 已加载 {len(configs)} 个仓库配置")
            return configs

        except Exception as exc:
            logger.error(f"[GitManager] 配置文件解析失败: {exc}")
            return []

    def sync_all(self, configs: Optional[list[RepoConfig]] = None) -> list[GitResult]:
        """同步所有配置的仓库。

        Args:
            configs: 仓库配置列表（为 None 时自动加载配置文件）

        Returns:
            GitResult 列表
        """
        if configs is None:
            configs = self.load_config()

        if not configs:
            logger.info("[GitManager] 无仓库需要同步")
            return []

        results: list[GitResult] = []
        for cfg in configs:
            result = self.sync_repo(cfg)
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        logger.info(f"[GitManager] 同步完成: {success_count}/{len(results)} 成功")
        return results

    def sync_repo(self, config: RepoConfig) -> GitResult:
        """同步单个仓库。

        Args:
            config: 仓库配置

        Returns:
            GitResult
        """
        domain_dir = self._src_dir / config.domain_name

        if domain_dir.exists() and (domain_dir / ".git").exists():
            # 已有 Git 仓库 → pull
            return self._git_pull(config, domain_dir)
        elif domain_dir.exists():
            # 目录存在但不是 Git 仓库 → 跳过
            logger.info(f"[GitManager] 领域 '{config.domain_name}' 为本地目录，跳过 Git 操作")
            return GitResult(
                domain=config.domain_name,
                success=True,
                action="skip",
                message="本地目录，非 Git 仓库",
            )
        else:
            # 新领域 → clone
            return self._git_clone(config, domain_dir)

    def get_commit_hash(self, domain: str) -> Optional[str]:
        """获取领域仓库的当前 commit hash。"""
        domain_dir = self._src_dir / domain
        if not (domain_dir / ".git").exists():
            return None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(domain_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def get_remote_url(self, domain: str) -> Optional[str]:
        """获取领域仓库的远程 URL。"""
        domain_dir = self._src_dir / domain
        if not (domain_dir / ".git").exists():
            return None
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(domain_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def is_git_repo(self, domain: str) -> bool:
        """检查领域目录是否为 Git 仓库。"""
        return (self._src_dir / domain / ".git").exists()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _git_clone(self, config: RepoConfig, target_dir: Path) -> GitResult:
        """执行 git clone。"""
        try:
            logger.info(f"[GitManager] 克隆仓库: {config.repo_url} → {target_dir}")
            cmd = [
                "git", "clone",
                "--branch", config.branch,
                "--single-branch",
                "--depth", "1",
                config.repo_url,
                str(target_dir),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            if result.returncode != 0:
                error = result.stderr.strip() or "未知错误"
                logger.error(f"[GitManager] 克隆失败 ({config.domain_name}): {error}")
                return GitResult(
                    domain=config.domain_name,
                    success=False,
                    action="clone",
                    error=error,
                )

            commit_hash = self.get_commit_hash(config.domain_name)
            logger.info(f"[GitManager] ✅ 克隆成功: {config.domain_name} ({commit_hash})")
            return GitResult(
                domain=config.domain_name,
                success=True,
                action="clone",
                commit_hash=commit_hash,
                message=f"克隆完成: {config.repo_url}",
            )

        except subprocess.TimeoutExpired:
            logger.error(f"[GitManager] 克隆超时 ({self._timeout}s): {config.domain_name}")
            return GitResult(
                domain=config.domain_name,
                success=False,
                action="clone",
                error=f"操作超时 ({self._timeout}s)",
            )
        except Exception as exc:
            logger.error(f"[GitManager] 克隆异常 ({config.domain_name}): {exc}")
            return GitResult(
                domain=config.domain_name,
                success=False,
                action="clone",
                error=str(exc),
            )

    def _git_pull(self, config: RepoConfig, domain_dir: Path) -> GitResult:
        """执行 git pull。"""
        try:
            logger.info(f"[GitManager] 拉取更新: {config.domain_name}")
            cmd = ["git", "pull", "--ff-only"]
            result = subprocess.run(
                cmd,
                cwd=str(domain_dir),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            if result.returncode != 0:
                error = result.stderr.strip() or "未知错误"
                logger.error(f"[GitManager] 拉取失败 ({config.domain_name}): {error}")
                return GitResult(
                    domain=config.domain_name,
                    success=False,
                    action="pull",
                    error=error,
                )

            commit_hash = self.get_commit_hash(config.domain_name)
            already_up_to_date = "Already up to date" in result.stdout or "已经是最新" in result.stdout
            msg = "已是最新" if already_up_to_date else "拉取成功"

            logger.info(f"[GitManager] ✅ {msg}: {config.domain_name} ({commit_hash})")
            return GitResult(
                domain=config.domain_name,
                success=True,
                action="pull",
                commit_hash=commit_hash,
                message=msg,
            )

        except subprocess.TimeoutExpired:
            logger.error(f"[GitManager] 拉取超时 ({self._timeout}s): {config.domain_name}")
            return GitResult(
                domain=config.domain_name,
                success=False,
                action="pull",
                error=f"操作超时 ({self._timeout}s)",
            )
        except Exception as exc:
            logger.error(f"[GitManager] 拉取异常 ({config.domain_name}): {exc}")
            return GitResult(
                domain=config.domain_name,
                success=False,
                action="pull",
                error=str(exc),
            )
