#!/usr/bin/env python3
"""StudyAgent image skill: run the unmodified OpenAI imagegen CLI against AIHUB with the tripo-cli image key."""
import os, runpy, sys
key = os.environ.get("STUDY_IMAGE_API_KEY", "").strip()
if not key:
    sys.exit("STUDY_IMAGE_API_KEY is not set")
os.environ["OPENAI_API_KEY"] = key
os.environ["OPENAI_BASE_URL"] = os.environ.get("STUDY_IMAGE_BASE_URL", "https://aihub.gz4399.com/v1")
here = os.path.dirname(os.path.abspath(__file__))
sys.argv[0] = os.path.join(here, "..", "vendor", "openai_image_gen.py")
runpy.run_path(sys.argv[0], run_name="__main__")
