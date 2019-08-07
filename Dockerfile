FROM bang6:5000/base_x86

WORKDIR /opt

COPY requirements.txt ./
RUN pip install --index-url https://projects.bigasterisk.com/ --extra-index-url https://pypi.org/simple -r requirements.txt

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y tzdata

# not needed for py2
#RUN pip install -U 'https://github.com/drewp/cyclone/archive/python3.zip?v3'

COPY *.py *.html *.css *.js *.n3 ./


EXPOSE 9048:9048

CMD [ "python3", "./diarybot2.py" ]
