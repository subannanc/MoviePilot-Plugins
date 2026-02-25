from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from app.utils.string import StringUtils
from app.helper.plugin import PluginHelper
from app.core.config import settings
from app.core.plugin import PluginManager
from app.db.systemconfig_oper import SystemConfigOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import SystemConfigKey


class CleanLogs(_PluginBase):
    # 插件名称
    plugin_name = "插件日志清理"
    # 插件描述
    plugin_desc = "定时清理插件产生的日志"
    # 插件图标
    plugin_icon = "clean.png"
    # 插件版本
    plugin_version = "1.3"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "cleanlogs_"
    # 加载顺序
    plugin_order = 50
    # 可使用的用户级别
    auth_level = 1

    _enable = False
    _onlyonce = False
    _cron = '30 3 * * *'
    _selected_ids: List[str] = []
    _rows = 300

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config is not None:
            self._enable = bool(config.get('enable', self._enable))
            self._selected_ids = config.get('selected_ids', self._selected_ids) or []
            try:
                self._rows = max(0, int(config.get('rows', self._rows)))
            except (TypeError, ValueError):
                self._rows = 300
            self._onlyonce = bool(config.get('onlyonce', False))
            self._cron = config.get('cron', self._cron)

        if not self._enable and not self._onlyonce:
            logger.info("插件日志清理未启用，跳过定时任务注册")
            return

        # 定时服务
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._onlyonce = False
            self.update_config({
                "onlyonce": self._onlyonce,
                "rows": self._rows,
                "enable": self._enable,
                "selected_ids": self._selected_ids,
                "cron": self._cron,
            })
            self._scheduler.add_job(func=self._task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="插件日志清理")
            logger.info("插件日志清理立即运行任务已注册")
        if self._enable and self._cron:
            try:
                self._scheduler.add_job(func=self._task,
                                        trigger=CronTrigger.from_crontab(self._cron),
                                        name="插件日志清理")
                logger.info(f"插件日志清理定时任务已注册，周期：{self._cron}")
            except Exception as err:
                logger.error(f"插件日志清理, 定时任务配置错误：{str(err)}")

        # 启动任务
        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def _task(self):
        logger.info("开始执行插件日志清理任务")
        clean_plugin = [str(pid).lower() for pid in self._selected_ids if pid]
        log_dir = settings.LOG_PATH / Path("plugins")

        if not log_dir.exists():
            logger.debug(f"日志目录不存在: {log_dir}")
            return

        if not clean_plugin:
            # 优先按真实日志文件发现，避免因插件未加载导致无法清理
            # 同时兼容只剩轮转日志（无主日志）场景
            discovered = set()
            for log_file in log_dir.glob("*.log"):
                discovered.add(log_file.stem.lower())
            for rotated_file in log_dir.glob("*.log.*"):
                marker = ".log."
                if marker in rotated_file.name:
                    discovered.add(rotated_file.name.split(marker, 1)[0].lower())
            clean_plugin = sorted(discovered)
            if not clean_plugin:
                local_plugins = PluginManager().get_local_plugins()
                for plugin in local_plugins:
                    clean_plugin.append(plugin.id.lower())

        for plugin_id in clean_plugin:
            log_name = f"{plugin_id.lower()}.log"
            log_path = log_dir / log_name

            deleted_rotated = 0
            for rotated in log_dir.glob(f"{log_name}.*"):
                # 删除所有轮转后缀，兼容 .log.1 / .log.2026-xx 等形式
                if rotated.is_file():
                    try:
                        rotated.unlink()
                        deleted_rotated += 1
                    except Exception as err:
                        logger.error(f"删除日志文件失败: {rotated}: {err}")

            if deleted_rotated > 0:
                logger.info(f"已删除 {plugin_id} 轮替日志文件 {deleted_rotated} 个")

            if not log_path.exists():
                logger.debug(f"{plugin_id} 日志文件不存在")
                continue

            try:
                with open(log_path, 'rb') as file:
                    lines = file.readlines()

                if self._rows == 0:
                    tail_lines = []
                else:
                    tail_lines = lines[-min(self._rows, len(lines)):]

                with open(log_path, 'wb') as file:
                    file.writelines(tail_lines)

                deleted_lines = max(0, len(lines) - len(tail_lines))
                if deleted_lines > 0:
                    logger.info(f"已清理 {plugin_id} {deleted_lines} 行日志")
            except Exception as err:
                logger.error(f"清理日志失败: {log_path}: {err}")


    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 已安装插件
        local_plugins = self.get_local_plugins()
        # 编历 local_plugins，生成插件类型选项
        plugin_options = []

        for plugin_id in list(local_plugins.keys()):
            local_plugin = local_plugins.get(plugin_id)
            plugin_options.append({
                "title": f"{local_plugin.get('plugin_name')} v{local_plugin.get('plugin_version')}",
                "value": local_plugin.get("id")
            })

        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '定时删除日志',
                                            'placeholder': '5位cron表达式'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'rows',
                                            'label': '保留Top行数',
                                            'placeholder': '300'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'model': 'selected_ids',
                                            'label': '删除插件日志,不指定默认全选',
                                            'items': plugin_options
                                        }
                                    }
                                ]
                            }
                        ]
                    }, {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '谢谢t佬的指点。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enable": self._enable,
            "onlyonce": self._onlyonce,
            "rows": self._rows,
            "cron": self._cron,
            "selected_ids": self._selected_ids,
        }

    @staticmethod
    def get_local_plugins():
        """
        获取本地插件
        """
        # 已安装插件
        install_plugins = SystemConfigOper().get(SystemConfigKey.UserInstalledPlugins) or []

        local_plugins = {}
        # 线上插件列表
        markets = settings.PLUGIN_MARKET.split(",")
        for market in markets:
            online_plugins = PluginHelper().get_plugins(market) or {}
            for pid, plugin in online_plugins.items():
                if pid in install_plugins:
                    local_plugin = local_plugins.get(pid)
                    if local_plugin:
                        if StringUtils.compare_version(local_plugin.get("plugin_version"), plugin.get("version")) < 0:
                            local_plugins[pid] = {
                                "id": pid,
                                "plugin_name": plugin.get("name"),
                                "repo_url": market,
                                "plugin_version": plugin.get("version")
                            }
                    else:
                        local_plugins[pid] = {
                            "id": pid,
                            "plugin_name": plugin.get("name"),
                            "repo_url": market,
                            "plugin_version": plugin.get("version")
                        }

        return local_plugins

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
