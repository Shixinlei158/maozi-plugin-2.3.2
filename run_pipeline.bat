@echo off
cd /d C:\ozon_pipeline
python -m ozon_pipeline.cli expand-seed-pool-network --process-limit 100 --max-depth 1 --max-sellers 10 > C:\Users\10200\pipeline_out.txt 2>&1
