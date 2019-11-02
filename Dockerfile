FROM bang6:5000/base_x86

WORKDIR /opt

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y tzdata

COPY requirements.txt ./
RUN pip3 install --index-url https://projects.bigasterisk.com/ --extra-index-url https://pypi.org/simple -r requirements.txt
RUN pip3 install -U 'https://github.com/drewp/cyclone/archive/python3.zip?v3'

RUN git clone -b drewp-commands https://github.com/drewp/slack-sansio.git
RUN cp /opt/slack-sansio/slack/methods.py /usr/local/lib/python3.6/dist-packages/slack/methods.py

COPY *.py *.html *.js *.n3 ./
COPY dist/ ./dist/

EXPOSE 9048:9048

CMD [ "python3", "./diarybot2.py" ]
