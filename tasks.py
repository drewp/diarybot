from invoke import task  # pytype: disable=import-error

JOB = 'diarybot'
PORT = 9048
TAG = f'bang6:5000/{JOB}_x86:latest'

@task
def build_image(ctx):
    ctx.run(f'npm run build')
    ctx.run(f'docker build --quiet --network=host -t {TAG} .')

@task(pre=[build_image])
def push_image(ctx):
    ctx.run(f'docker push {TAG}')

@task(pre=[build_image])
def shell(ctx):
    ctx.run(f'docker run --name={JOB}_shell --rm -it --cap-add SYS_PTRACE -v `pwd`:/opt --net=host {TAG} /bin/bash', pty=True)

@task(pre=[build_image])
def local_run(ctx):
    ctx.run(f'docker run --name={JOB}_local --rm -it --net=host -v `pwd`:/opt {TAG} '
            f'python3 diarybot2.py -v --no-chat --drew-bot',
            pty=True, echo=True)

@task(pre=[build_image])
def check(ctx):
    ctx.run(f'docker run --name={JOB}_local --rm -it --net=host -v `pwd`:/opt {TAG} pytype --python-version 3.6 *.py', pty=True)

@task(pre=[build_image])
def buildIndex(ctx):
    ctx.run(f'docker run --name={JOB}_index --rm --net=host -v `pwd`:/opt {TAG} python3 buildIndex.py', pty=True)

@task(pre=[push_image])
def redeploy(ctx):
    ctx.run(f'supervisorctl -s http://bang:9001/ restart {JOB}_{PORT}')

@task
def backup(ctx, outdir='backup'):
    """needs pkg mongodb-clients"""
    import json, os, datetime
    res = ctx.run(f'mongo bang/diarybot --eval "db.getCollectionNames()"')
    colls = json.loads(res.stdout[res.stdout.find('['):])
    outPrefix = os.path.join(outdir, datetime.date.today().isoformat() + '_')
    for coll in colls:
        ctx.run(f'mongoexport --host=bang --db=diarybot --collection={coll} --out={outPrefix}{coll}.json')
