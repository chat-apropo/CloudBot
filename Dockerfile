FROM python:3.9.19-bullseye

WORKDIR /app
COPY . /app

RUN \
  apt-get update && \
  apt-get install -y --no-install-recommends \
    enchant-2 \
    libenchant-2-2 \

  pip install --no-cache-dir -r requirements.txt && \
  pip install --no-cache-dir -r my_requirements.txt

CMD ["python", "-m", "cloudbot"]
