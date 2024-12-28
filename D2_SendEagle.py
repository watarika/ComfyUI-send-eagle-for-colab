import json
import os
import numpy as np
import json
from typing import Dict, Optional

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from datetime import datetime
from zoneinfo import ZoneInfo

import folder_paths

from .modules.util import util
from .modules.eagle_api import EagleAPI
from .modules.params_extractor import ParamsExtractor

from .my_types import TNodeParams, TGenInfo

FORCE_WRITE_PROMPT = False

class D2_SendEagle:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.output_folder = ""
        self.subfolder_name = ""
        self.eagle_server_url = os.environ.get("EAGLE_SERVER_URL", "http://localhost:41595")
        self.comfyui_url = os.environ.get("EAGLE_COMFYUI_URL", "http://localhost:8188")
        self.eagle_api = EagleAPI(self.eagle_server_url)


    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                # ポジティブプロンプト
                "positive": (
                    "STRING",
                    {"forceInput": True, "multiline": True},
                ),
                # ネガティブプロンプト
                "negative": (
                    "STRING",
                    {"forceInput": True, "multiline": True},
                ),
                "format": (["webp", "png", "jpeg"],),
                # webpの時に可逆（lossless）不可逆（lossy）どちらにするか
                "lossless_webp": (
                    "BOOLEAN",
                    {"default": True, "label_on": "lossless", "label_off": "lossy"},
                ),
                # webp lossy または jpeg の時の圧縮率
                "compression": (
                    "INT",
                    {"default": 90, "min": 1, "max": 100, "step": 1},
                ),
                # プロンプトやモデルをEagleタグに保存するか
                "save_tags": ([
                    "None",
                    "Prompt + Checkpoint",
                    "Prompt",
                    "Checkpoint",
                ],),
                # 保存するファイル名
                "filename_template": (
                    "STRING",
                    {"multiline": False, "default":"{model}-{seed}"},
                ),
                # Eagleフォルダ
                "eagle_folder": (
                    "STRING",
                    {"default": ""}
                ),
                # プレビュー表示するか
                "preview": (
                    "BOOLEAN",
                    {"default": True, "label_on": "ON", "label_off": "OFF"},
                ),
                # Eagleに送信せずに保存のみ行うか
                "save_only": (
                    "BOOLEAN",
                    {"default": False, "label_on": "ON", "label_off": "OFF"},
                ),
            },
            "optional":{
                # その他メモ
                "memo_text": (
                    "STRING",
                    {"multiline": True},
                ),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("STRING", "STRING", "IMAGE")
    RETURN_NAMES = ("positive", "negative", "IMAGE", )
    FUNCTION = "add_item"
    OUTPUT_NODE = True
    CATEGORY = "D2"

    # ######################
    # ノード実行で呼ばれる関数
    # Eagleに画像を送る
    def add_item(
        self,
        images,
        format = "webp",
        lossless_webp = False,
        save_tags = "None",
        filename_template = "{model}-{width}-{height}-{seed}",
        eagle_folder = "",
        compression = 80,
        positive = "",
        negative = "",
        preview = True,
        save_only = False,
        memo_text = "",
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None,
    ):
        self.output_folder, self.subfolder_name = self.get_output_folder()

        results = list()
        params: TNodeParams = {
            "format": format,
            "lossless_webp": lossless_webp,
            "save_tags": save_tags,
            "filename_template": filename_template,
            "eagle_folder": eagle_folder,
            "compression": compression,
            "positive": positive,
            "negative": negative,
            "save_only": save_only,
            "memo_text": memo_text,
            "prompt": prompt,
            "extra_pnginfo": extra_pnginfo,
        }

        # imagesのサイズが1の場合（batch_sizeが1の場合）はファイル名にカウントをつけない
        if len(images) == 1:
            results.append(self.create_image_object(images[0], params, None))
        else:
            count = 0 # 0オリジン
            for image in images:
                results.append(self.create_image_object(image, params, count))
                count += 1

        if(preview):
            return {
                "ui": {"images": results},
                "result": (positive, negative, images,)
            }

        return {
            "result": (positive, negative, images,)
        }


    # ######################
    # イメージオブジェクトを作成
    def create_image_object(self, image, params:TNodeParams, count) -> dict:
        normalized_pixels = 255.0 * image.cpu().numpy()
        img = Image.fromarray(np.clip(normalized_pixels, 0, 255).astype(np.uint8))

        # 生成パラメータ整理
        paramsExtractor = self.create_generate_params(img, params)
        # 必要な生成パラメーターをまとめたもの
        gen_info = paramsExtractor.gen_info
        # EagleやPNGInfo用に整形したもの
        formated_info = paramsExtractor.format_info(params["memo_text"])

        # print("generate_params", gen_info)
        # print("format_info", formated_info)

        # 画像をローカルに保存
        file_name, file_full_path = self.save_image(img, params, gen_info, formated_info, count)

        # Eagleに画像を送る
        if not params["save_only"]:
            # Eagleフォルダが指定されているならフォルダIDを取得
            folder_id = self.eagle_api.find_or_create_folder(params["eagle_folder"])

            # Eagleに送るURLを作成
            url = f"{self.comfyui_url}/api/view?filename={file_name}&type={self.type}&subfolder={self.subfolder_name}"

            # Eagleに送る情報を作成
            item = {
                "url": url,
                "name": file_name,
                "annotation": formated_info,
                "tags": [],
            }

            # タグを取得
            item["tags"] = self.get_tags(params, gen_info)

            _ret = self.eagle_api.add_item_from_url(data=item, folder_id=folder_id)        

        return {
            "filename": file_name, "subfolder": self.subfolder_name, "type": self.type
        }

    # ######################
    # 登録タグを取得
    def get_tags(self, params:TNodeParams, gen_info:TGenInfo) -> list:
        if(params["save_tags"] == "Prompt + Checkpoint"):
          return [*util.get_prompt_tags(gen_info["positive"]), gen_info["model_name"]]

        elif(params["save_tags"] == "Prompt"):
          return util.get_prompt_tags(gen_info["positive"])

        elif(params["save_tags"] == "Checkpoint"):
          return [gen_info["model_name"]]

        return []


    # ######################
    # 画像をローカルに保存
    def save_image(self, img, params:TNodeParams, gen_info:TGenInfo, formated_info:str, count):
        file_name = ""
        file_full_path = ""

        if params["format"] == "webp":
            # Save webp image file
            file_name = self.get_filename(params["filename_template"], 'webp', gen_info, count)
            file_full_path = os.path.join(self.output_folder, file_name)

            exif = util.get_exif_from_prompt(img, formated_info, params["extra_pnginfo"], params["prompt"])

            img.save(
                file_full_path,
                quality = params["compression"],
                exif = exif,
                lossless = params["lossless_webp"],
            )

        elif params["format"] == "jpeg":
            # Save jpeg image file
            file_name = self.get_filename(params["filename_template"], 'jpeg', gen_info, count)
            file_full_path = os.path.join(self.output_folder, file_name)

            exif = util.get_exif_from_prompt(img, formated_info, params["extra_pnginfo"], params["prompt"])

            img.save(
                file_full_path,
                quality = params["compression"],
                optimize = True,
                exif = exif,
            )

        else:
            # Save png image file
            file_name = self.get_filename(params["filename_template"], 'png', gen_info, count)
            file_full_path = os.path.join(self.output_folder, file_name)

            metadata = PngInfo()

            if params["prompt"] is not None:
                metadata.add_text("prompt", json.dumps(params["prompt"]))
            if params["extra_pnginfo"] is not None:
                for x in params["extra_pnginfo"]:
                    metadata.add_text(x, json.dumps(params["extra_pnginfo"][x]))

            img.save(file_full_path, pnginfo=metadata, compress_level=4)

        return file_name, file_full_path


    # ######################
    # 画像保存パスを取得
    def get_output_folder(self):
        subfolder_name = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")

        # 画像保存用フォルダが無ければ作成
        output_folder = os.path.join(self.output_dir, subfolder_name)

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        return output_folder, subfolder_name


    # ######################
    # 生成パラメーターを取得
    def create_generate_params(self, img, params:TNodeParams) -> ParamsExtractor:
        # print("[SendEagle] create_generate_params - ", params )
        paramsExtractor = ParamsExtractor(params)
        paramsExtractor.gen_info["width"] = img.width
        paramsExtractor.gen_info["height"] = img.height

        return paramsExtractor


    # ######################
    # ファイルネームを取得
    def get_filename(self, template:str, ext:str, gen_info:TGenInfo, count) -> str:
        base = template.format(
          width = gen_info["width"],
          height = gen_info["height"],
          model = gen_info["model_name"],
          steps = gen_info["steps"],
          seed = gen_info["seed"],
        )

        if count is None:
            return f"{util.get_datetime_str_msec()}-{base}.{ext}"
        else:
            return f"{util.get_datetime_str_msec()}-{base}({count}).{ext}"
