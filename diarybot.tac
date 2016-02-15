from twisted.application import service, internet
from twisted.web import static, server
import diarybot2

application = service.Application("diarybot")
diarybot2.bots = diarybot2.makeBots(application, "bots-secret.n3")


service = internet.TCPServer(9048, diarybot2.site)
service.setServiceParent(application)

