FROM node:latest

WORKDIR /app

RUN curl -sS -o - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64]  http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get -y update && \
    apt-get -y install google-chrome-stable && \
    apt-get clean

RUN wget https://chromedriver.storage.googleapis.com/2.41/chromedriver_linux64.zip && \
    unzip chromedriver_linux64.zip && \
    chown root:root chromedriver && \
    chmod +x chromedriver && \
    mv chromedriver /usr/bin/

ENV NODE_PATH /app/node_modules

RUN npm install selenium-webdriver chromedriver lodash assert request

COPY mozlenium.js /app/node_modules/

COPY entrypoint.sh /app

ENTRYPOINT [ "/app/entrypoint.sh" ]
