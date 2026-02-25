from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app import schemas
from app.chain.media import MediaChain
from app.chain.tmdb import TmdbChain
from app.core.config import settings
from app.core.metainfo import MetaInfoPath
from app.helper.directory import DirectoryHelper
from app.modules.filemanager.transhandler import TransHandler
from app.plugins import _PluginBase
from app.schemas import MediaType


class TargetPathRequest(BaseModel):
    path: str = Field(..., description="视频文件或目录路径")
    model_config = {"extra": "ignore"}


class MediaTargetPathApi(_PluginBase):
    plugin_name = "媒体目标路径查询 API"
    plugin_desc = "根据视频路径识别媒体并计算重命名后的目标媒体库路径"
    plugin_version = "1.0.0"
    plugin_author = "honue"
    auth_level = 1
    plugin_config_prefix = "mediatargetpathapi_"

    _enabled: bool = False

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", True))

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        return [
            {
                "path": "/query_target_path",
                "endpoint": self.query_target_path_get,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "计算媒体整理目标路径（GET）",
                "description": "通过 query 参数 path 返回目标路径",
            },
            {
                "path": "/query_target_path",
                "endpoint": self.query_target_path,
                "methods": ["POST"],
                "auth": "apikey",
                "summary": "计算媒体整理目标路径（POST）",
                "description": "通过 JSON body 的 path 返回目标路径",
            }
        ]

    def _query_target_path(self, path: str) -> schemas.Response:
        src_path = Path(path)
        meta = MetaInfoPath(src_path)
        mediainfo = MediaChain().recognize_media(meta=meta)
        if not mediainfo:
            return schemas.Response(success=False, message="未识别到媒体信息")

        target_dir_conf = DirectoryHelper().get_dir(
            media=mediainfo,
            storage="local",
            src_path=src_path,
        )
        if not target_dir_conf:
            return schemas.Response(success=False, message="未找到有效的媒体库目录")

        handler = TransHandler()
        base_dir = handler.get_dest_dir(mediainfo=mediainfo, target_dir=target_dir_conf)

        episodes_info = None
        if mediainfo.type == MediaType.TV:
            season_num = mediainfo.season
            if season_num is None and meta.season_seq and meta.season_seq.isdigit():
                season_num = int(meta.season_seq)
            if season_num is None:
                season_num = 1
            episodes_info = TmdbChain().tmdb_episodes(
                tmdbid=mediainfo.tmdb_id,
                season=season_num,
                episode_group=mediainfo.episode_group,
            )

        rename_format = settings.RENAME_FORMAT(mediainfo.type)
        file_ext = src_path.suffix or Path(meta.title).suffix
        rename_path = handler.get_rename_path(
            template_string=rename_format,
            rename_dict=handler.get_naming_dict(
                meta=meta,
                mediainfo=mediainfo,
                episodes_info=episodes_info,
                file_ext=file_ext,
            ),
            path=base_dir,
        )

        resolved_filetype = "dir" if src_path.is_dir() else "file"

        if resolved_filetype == "dir":
            media_root = DirectoryHelper.get_media_root_path(rename_format, rename_path)
            target_path = media_root or rename_path
        else:
            target_path = rename_path

        return schemas.Response(success=True, data={
            "target_path": target_path.as_posix(),
        })

    def query_target_path(self, payload: TargetPathRequest) -> schemas.Response:
        return self._query_target_path(payload.path)

    def query_target_path_get(self, path: str) -> schemas.Response:
        return self._query_target_path(path)

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用插件",
                        },
                    }
                ],
            }
        ], {
            "enabled": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "接口地址：/api/v1/plugin/MediaTargetPathApi/query_target_path（APIKEY鉴权）",
                },
            }
        ]

    def stop_service(self):
        pass
