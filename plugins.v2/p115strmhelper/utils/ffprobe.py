__all__ = ["FFprobeUtils"]


from typing import Tuple, Optional, Dict, Any, List
from pathlib import Path
from urllib.parse import unquote
from subprocess import run, TimeoutExpired

from orjson import loads, JSONDecodeError


class FFprobeUtils:
    """
    FFprobe 工具类
    """

    FFPROBE_TIMEOUT_SEC = 30

    _VIDEO_CODEC_MAP = {
        "h264": "H264",
        "avc": "H264",
        "hevc": "H265",
        "h265": "H265",
        "av1": "AV1",
        "vp9": "VP9",
        "vp8": "VP8",
        "mpeg2video": "MPEG2",
        "vc1": "VC1",
        "mpeg4": "MPEG4",
    }

    _AUDIO_CODEC_MAP = {
        "aac": "AAC",
        "eac3": "EAC3",
        "ac3": "AC3",
        "dts": "DTS",
        "truehd": "TrueHD",
        "flac": "FLAC",
        "opus": "OPUS",
        "mp3": "MP3",
        "vorbis": "Vorbis",
    }

    _HEIGHT_SNAP_TIERS: Tuple[Tuple[int, int], ...] = (
        (4320, 48),
        (2880, 48),
        (2160, 48),
        (1920, 40),
        (1800, 40),
        (1600, 40),
        (1536, 40),
        (1440, 40),
        (1366, 32),
        (1280, 32),
        (1200, 32),
        (1152, 32),
        (1080, 40),
        (1050, 32),
        (1024, 32),
        (960, 32),
        (900, 28),
        (864, 28),
        (854, 24),
        (800, 24),
        (768, 24),
        (720, 32),
        (704, 24),
        (640, 24),
        (600, 20),
        (576, 20),
        (540, 20),
        (528, 20),
        (512, 20),
        (506, 20),
        (480, 24),
        (468, 20),
        (456, 20),
        (432, 20),
        (408, 16),
        (400, 16),
        (360, 16),
        (320, 16),
        (288, 16),
        (272, 16),
        (240, 16),
        (228, 12),
        (180, 12),
        (168, 12),
        (144, 12),
        (120, 12),
    )

    _HEIGHT_FORMAT_BUCKETS: Tuple[Tuple[int, str], ...] = tuple(
        (height, f"{height}p") for height, _ in _HEIGHT_SNAP_TIERS
    )

    _DV_CODEC_TAGS = frozenset({"dvh1", "dvhe", "dva1", "dvav"})

    @staticmethod
    def _parse_frame_rate(rate: Optional[str]) -> Optional[str]:
        """
        将 ffprobe 的帧率字符串转为简短展示用字符串

        :param rate: 如 24000/1001 或 30
        :return: 如 23.976 或 30
        """
        if not rate or rate in ("0/0", "N/A"):
            return None
        if "/" in rate:
            parts = rate.split("/", 1)
            try:
                num = int(parts[0].strip())
                den = int(parts[1].strip())
            except (ValueError, IndexError):
                return None
            if den == 0:
                return None
            value = num / den
        else:
            try:
                value = float(rate)
            except ValueError:
                return None
        if abs(value - round(value)) < 1e-3:
            return str(int(round(value)))
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or None

    @staticmethod
    def _snap_height_to_standard(height: int) -> int:
        """
        将因 mod16 裁剪、轻微缩放导致的高度吸附到常见标准值

        :param height: ffprobe 报告的帧高度
        :return: 吸附后的高度（未命中任一容差则原样返回）
        """
        for target, tolerance in FFprobeUtils._HEIGHT_SNAP_TIERS:
            if abs(height - target) <= tolerance:
                return target
        return height

    @staticmethod
    def _height_to_video_format(height: Optional[int]) -> Optional[str]:
        """
        根据视频高度生成分辨率标签：先吸附再按降序分档，未命中则用「{height}p」
        """
        if height is None:
            return None
        try:
            h = int(height)
        except (TypeError, ValueError):
            return None
        if h <= 0:
            return None
        h = FFprobeUtils._snap_height_to_standard(h)
        for min_h, label in FFprobeUtils._HEIGHT_FORMAT_BUCKETS:
            if h >= min_h:
                return label
        return f"{h}p"

    @staticmethod
    def _map_video_codec(codec_name: Optional[str]) -> Optional[str]:
        if not codec_name:
            return None
        key = codec_name.lower().strip()
        return FFprobeUtils._VIDEO_CODEC_MAP.get(key, codec_name.upper())

    @staticmethod
    def _map_audio_codec(codec_name: Optional[str]) -> Optional[str]:
        if not codec_name:
            return None
        key = codec_name.lower().strip()
        return FFprobeUtils._AUDIO_CODEC_MAP.get(key, codec_name.upper())

    @staticmethod
    def _video_stream_hdr_flags(video_s: Dict[str, Any]) -> Tuple[bool, bool]:
        """
        从视频流 side_data 与 codec_tag 判断是否含 Dolby Vision / HDR10+ 元数据

        :param video_s: ffprobe 单路视频流 dict
        :return: (has_dovi, has_hdr10plus)
        """
        has_dovi = False
        has_hdr10plus = False
        tag = (video_s.get("codec_tag_string") or "").strip().lower()
        if tag in FFprobeUtils._DV_CODEC_TAGS:
            has_dovi = True
        side_list = video_s.get("side_data_list")
        if not isinstance(side_list, list):
            return has_dovi, has_hdr10plus
        for item in side_list:
            if not isinstance(item, dict):
                continue
            sdt = (item.get("side_data_type") or "").lower()
            if "dovi" in sdt or "dolby vision" in sdt:
                has_dovi = True
            if "smpte2094-40" in sdt or "2094-40" in sdt:
                has_hdr10plus = True
            if "hdr10+" in sdt and "dynamic" in sdt:
                has_hdr10plus = True
        return has_dovi, has_hdr10plus

    @staticmethod
    def _infer_effect_from_video_stream(video_s: Dict[str, Any]) -> Optional[str]:
        """
        根据 ffprobe 色彩与 side_data 推断与 MoviePilot 模板变量 effect 对应的标签

        输出与常见资源命名接近的短标签，多个以空格连接（如 DoVi、HDR10+）

        :param video_s: ffprobe 单路视频流 dict
        :return: 供 rename_dict["effect"] 使用的字符串，无法判断则 None
        """
        has_dovi, has_hdr10plus = FFprobeUtils._video_stream_hdr_flags(video_s)
        ct = (video_s.get("color_transfer") or "").lower().strip()
        cp = (video_s.get("color_primaries") or "").lower().strip()

        tokens: List[str] = []
        if has_dovi:
            tokens.append("DoVi")
        if has_hdr10plus:
            tokens.append("HDR10+")
        if not has_dovi and not has_hdr10plus:
            if ct == "smpte2084":
                tokens.append("HDR10")
            elif "arib-std-b67" in ct:
                tokens.append("HLG")

        if not tokens:
            if ct == "bt709" and (not cp or cp == "bt709"):
                tokens.append("SDR")

        if not tokens:
            return None
        return " ".join(tokens)

    @staticmethod
    def _pick_video_audio_streams(
        streams: List[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        video_s: Optional[Dict[str, Any]] = None
        audio_s: Optional[Dict[str, Any]] = None
        for s in streams:
            if not isinstance(s, dict):
                continue
            ct = s.get("codec_type")
            if ct == "video" and video_s is None:
                video_s = s
            elif ct == "audio" and audio_s is None:
                audio_s = s
            if video_s is not None and audio_s is not None:
                break
        return video_s, audio_s

    @staticmethod
    def _probe_to_rename_fields(probe_json: Dict[str, Any]) -> Dict[str, str]:
        """
        从 ffprobe JSON 提取写入 rename_dict 的命名模板字段
        """
        out: Dict[str, str] = {}
        streams = probe_json.get("streams")
        if not isinstance(streams, list):
            return out
        video_s, audio_s = FFprobeUtils._pick_video_audio_streams(streams)
        if video_s:
            height = video_s.get("height")
            try:
                h_int = int(height) if height is not None else None
            except (TypeError, ValueError):
                h_int = None
            vf = FFprobeUtils._height_to_video_format(h_int)
            if vf:
                out["videoFormat"] = vf
            vc = FFprobeUtils._map_video_codec(video_s.get("codec_name"))
            if vc:
                out["videoCodec"] = vc
            fps = FFprobeUtils._parse_frame_rate(
                video_s.get("avg_frame_rate")
            ) or FFprobeUtils._parse_frame_rate(video_s.get("r_frame_rate"))
            if fps:
                out["fps"] = fps
            eff = FFprobeUtils._infer_effect_from_video_stream(video_s)
            if eff:
                out["effect"] = eff
        if audio_s:
            ac = FFprobeUtils._map_audio_codec(audio_s.get("codec_name"))
            if ac:
                out["audioCodec"] = ac
        return out

    @staticmethod
    def _normalize_strm_target(raw: str) -> str:
        """
        规范化 STRM 首行内容，便于 ffprobe 作为 -i 参数使用

        :param raw: 行内原始文本（已去掉首尾空白）
        :return: 规范化后的地址或路径，无效则空字符串
        """
        line = raw.strip()
        if not line:
            return ""
        if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
            line = line[1:-1].strip()
        if "%" in line:
            try:
                line = unquote(line)
            except Exception:
                pass
        return line.strip()

    @staticmethod
    def _resolve_probe_target(source_path: str) -> Tuple[Optional[str], str]:
        """
        普通文件直接返回路径；STRM 读取首条有效行并规范化后作为真实地址
        """
        p = Path(source_path)
        if p.suffix.lower() != ".strm":
            return source_path.strip(), ""
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            return None, f"读取 STRM 失败 {source_path}: {e}"
        for line in text.splitlines():
            line = line.strip()
            if not line or line.lstrip().startswith("#"):
                continue
            normalized = FFprobeUtils._normalize_strm_target(line)
            if normalized:
                return normalized, ""
        return None, f"STRM 内容为空 {source_path}"

    @staticmethod
    def _run_ffprobe(probe_target: str) -> Tuple[Optional[Dict[str, Any]], str]:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            "-i",
            probe_target,
        ]
        try:
            proc = run(
                cmd,
                capture_output=True,
                text=True,
                timeout=FFprobeUtils.FFPROBE_TIMEOUT_SEC,
            )
        except TimeoutExpired:
            return (
                None,
                f"ffprobe 超时({FFprobeUtils.FFPROBE_TIMEOUT_SEC}s) target={probe_target}",
            )
        except OSError as e:
            return None, f"无法执行 ffprobe: {e}"
        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or proc.stdout
            return (
                None,
                f"ffprobe 失败 rc={proc.returncode} target={probe_target} err={err[:500] if err else ''}",
            )
        try:
            return loads(proc.stdout), ""
        except JSONDecodeError as e:
            return None, f"ffprobe JSON 解析失败: {e}"

    @staticmethod
    def get_media_info(source_path: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        获取媒体信息
        """
        probe_target, error_message = FFprobeUtils._resolve_probe_target(source_path)
        if not probe_target:
            return None, error_message

        probe_json, error_message = FFprobeUtils._run_ffprobe(probe_target)
        if not probe_json:
            return None, error_message

        return FFprobeUtils._probe_to_rename_fields(probe_json), ""
