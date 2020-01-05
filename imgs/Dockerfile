FROM python:3.7-alpine

COPY print_files.py /scripts/print_files.py

RUN python3 -m compileall /scripts/print_files.py

WORKDIR /compile

CMD python3 /scripts/print_files.py
