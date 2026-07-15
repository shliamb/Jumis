#! worker/stt_sense/__init__.py
from stt_sense.stt_asr import FastTranscriber


stt = FastTranscriber()
__all__ = ['stt']

