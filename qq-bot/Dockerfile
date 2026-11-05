FROM python:3.11-slim

WORKDIR /app

# 国内服务器可用 --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY bot.py persona.py ./

CMD ["python", "-u", "bot.py"]
