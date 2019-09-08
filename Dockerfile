FROM python/slim

RUN set -eux; \
    curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt; \
    chmod +x lowvoicebot.py
ENTRYPOINT ["lowvoicebot.py"]