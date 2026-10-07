#!/usr/bin/env python3
"""Launch the shared chat implementation locally with an authenticated oc proxy."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] /
                       'charts/coco-supported/qwen-chat/files/qwen-chat.py'), run_name='__main__')
