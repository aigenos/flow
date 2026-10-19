@echo off
rem Start Ollama with a quantized KV cache so qwen3:8b at num_ctx=10240 fits
rem FULLY inside an 8 GB GPU (default fp16 KV spills ~1 GB to CPU and makes
rem hard episodes 5-10x slower). Leave this window open while the sweep runs.
set OLLAMA_FLASH_ATTENTION=1
set OLLAMA_KV_CACHE_TYPE=q8_0
echo Starting Ollama (flash attention + q8_0 KV cache)...
"%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
