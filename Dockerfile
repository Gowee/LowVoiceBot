FROM python:slim

RUN set -eux; \
    apt-get update; \
    apt-get install -y curl; \
    curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN chmod +x lowvoicebot.py
ENTRYPOINT ["/app/lowvoicebot.py"]
