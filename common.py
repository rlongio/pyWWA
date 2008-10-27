# Common stuff for the pyWWA ingestors...

from twisted.internet import reactor
from twisted.words.xish import domish
from twisted.python import log
from twisted.words.xish.xmlstream import STREAM_END_EVENT
from twisted.internet.task import LoopingCall

import secret

class JabberClient:

    def __init__(self, myJid):
        self.myJid = myJid
        self.xmlstream = None
        self.authenticated = False

    def authd(self,xs):
        log.msg("Logged into Jabber Chat Server!")

        self.xmlstream = xs
        self.xmlstream.rawDataInFn = self.rawDataInFn
        self.xmlstream.rawDataOutFn = self.rawDataOutFn
        presence = domish.Element(('jabber:client','presence'))
        presence.addElement('status').addContent('Online')
        self.xmlstream.send(presence)
        self.authenticated = True

        lc = LoopingCall(self.keepalive)
        lc.start(60)
        self.xmlstream.addObserver(STREAM_END_EVENT, lambda _: lc.stop())

    def keepalive(self):
        self.xmlstream.send(' ')

    def _disconnect(self, xs):
        log.msg("SETTING authenticated to false!")
        self.authenticated = False

    def sendMessage(self, body, html=None, to_user=secret.iembot_user):
        if (not self.authenticated):
            log.msg("No Connection, Lets wait and try later...")
            reactor.callLater(3, self.sendMessage, body, html, to_user)
            return
        message = domish.Element(('jabber:client','message'))
        message['to'] = '%s@%s' % (to_user, secret.chatserver)
        message['type'] = 'chat'

        # message.addElement('subject',None,subject)
        message.addElement('body',None,body)
        if (html is not None):
            message.addRawXml("<html xmlns='http://jabber.org/protocol/xhtml-im'><body xmlns='http://www.w3.org/1999/xhtml'>"+ html +"</body></html>")
        self.xmlstream.send(message)


    def debug(self, elem):
        log.msg( elem.toXml().encode('utf-8') )

    def rawDataInFn(self, data):
        print 'RECV', unicode(data,'utf-8','ignore').encode('ascii', 'replace')
    def rawDataOutFn(self, data):
        if (data == ' '): return 
        print 'SEND', unicode(data,'utf-8','ignore').encode('ascii', 'replace')
