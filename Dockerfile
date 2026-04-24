FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-liberation \
    fontconfig \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/share/fonts/truetype/playfair && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay-Bold.ttf" \
    -O /usr/share/fonts/truetype/playfair/PlayfairDisplay-Bold.ttf && \
    fc-cache -f -v

RUN mkdir -p /home/covertidos/app \
             /home/covertidos/assets \
             /home/covertidos/temp \
             /home/covertidos/output

WORKDIR /home/covertidos/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
