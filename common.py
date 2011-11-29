# Common stuff for the pyWWA ingestors...

from twisted.internet import reactor
from twisted.words.xish import domish
from twisted.python import log
from twisted.mail import smtp
from twisted.web import client
from twisted.words.xish.xmlstream import STREAM_END_EVENT
from twisted.internet.task import LoopingCall
from twisted.enterprise import adbapi

import secret
from twittytwister import twitter
import urllib
import simplejson
import traceback
import sys
import os
import StringIO
from email.MIMEText import MIMEText
import base64
import pg
import socket
from oauth import oauth

OAUTH_TOKENS = {}
OAUTH_CONSUMER = oauth.OAuthConsumer(secret.consumer_key, secret.consumer_secret)
BITLY = "http://api.bit.ly/shorten?version=2.0.1&longUrl=%s&login=iembot&apiKey="+ secret.bitly_key

def load_tokens(txn):
    txn.execute("SELECT * from oauth_tokens")
    for row in txn:
        OAUTH_TOKENS[ row['username'] ] = oauth.OAuthToken(
                            row['token'], row['secret'])

_dbpool = adbapi.ConnectionPool("twistedpg", database="mesosite", 
                               host=secret.dbhost, user=secret.dbuser,
                               password=secret.dbpass, cp_reconnect=True)
defer = _dbpool.runInteraction(load_tokens)
def stop_pool(res):
    _dbpool.close()
defer.addCallback(stop_pool)

def email_error(exp, message):
    """
    Helper function to generate error emails when necessary and hopefully
    not flood!
    """
    # Always log a message about our fun
    cstr = StringIO.StringIO()
    traceback.print_exc(file=cstr)
    cstr.seek(0)
    tbstr = cstr.read()
    log.msg( tbstr )
    log.msg( exp )
    log.msg( message )

    # Now, we may email....
    if os.environ.get('EMAILS') is None:
        os.environ['EMAILS'] = "10"
    if int(os.environ['EMAILS']) < 0:
        log.msg("NO EMAIL DUE TO LIMIT...")
        return
    os.environ['EMAILS'] = str( int(os.environ["EMAILS"]) - 1 )

    msg = MIMEText("""
Emails Left: %s  Host: %s

Exception:
%s
%s

Message:
%s
""" % (os.environ["EMAILS"], socket.gethostname(), tbstr, exp, message))
    msg['subject'] = '%s Traceback' % (sys.argv[0],)
    msg['From'] = secret.parser_user
    msg['To'] = secret.error_email
    smtp.sendmail("localhost", msg["From"], msg["To"], msg)

def tweet(channels, msg, url, extras={}):
    """
    Method to publish twitter messages
    """
    if secret.DISARM:
        channels = ['TEST',]
        return

    if url:
        url = url.replace("&amp;", "&").replace("#","%23")
        deffer = client.getPage(BITLY % (url, ) )
        deffer.addCallback(reallytweet, channels, msg, extras )
        deffer.addErrback(reallytweet, channels, msg, extras )
    else:
        reallytweet(None, channels, msg, extras)

def reallytweet(json, channels, msg, extras):
    """
    Actually, really publish this time!
    """
    tinyurl = ""
    if json and type(json) == type(""):
        j = simplejson.loads( json )
        if j.has_key('errorCode') and j['errorCode'] != 0 and j.has_key('errorMessage'):
            if j['errorCode'] != 500:
                email_error(str(j), "Problem with bitly")
        elif j.has_key('results'):
            tinyurl = j['results'][ j['results'].keys()[0] ]['shortUrl']
    # We are finally ready to tweet!
    for channel in channels:
        tuser = "iembot_%s" % (channel.lower(),)
        if not OAUTH_TOKENS.has_key(tuser):
            print "Unknown Twitter User, %s" % (tuser,)
            continue
        twt = "#%s %s %s" % (channel, msg[:112], tinyurl)
        deffer = twitter.Twitter(consumer=OAUTH_CONSUMER, token=OAUTH_TOKENS[tuser]).update( twt, None, extras)
        deffer.addCallback(tb, channel, twt)
        deffer.addErrback(twitterErrback, channel, twt)

def tb(x, channel, twt):
    print "TWEET [%s] RES: %s" % (twt, x) 
    
def twitterErrback(err, channel, twt):
    print "TWEET [%s] RES: %s" % (twt, err)
    log.msg( dir(err) ) 


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

    def sendMessage(self, body, html, to_user=secret.iembot_user):
        if (not self.authenticated):
            log.msg("No Connection, Lets wait and try later...")
            reactor.callLater(3, self.sendMessage, body, html, to_user)
            return
        message = domish.Element(('jabber:client','message'))
        message['to'] = '%s@%s' % (to_user, secret.chatserver)
        message['type'] = 'chat'

        # message.addElement('subject',None,subject)
        message.addElement('body',None,body)
        h = message.addElement('html','http://jabber.org/protocol/xhtml-im')
        b = h.addElement('body', 'http://www.w3.org/1999/xhtml')
        b.addRawXml( html or body )
        self.xmlstream.send(message)

    def debug(self, elem):
        log.msg( elem.toXml().encode('utf-8') )

    def rawDataInFn(self, data):
        print 'RECV', unicode(data,'utf-8','ignore').encode('ascii', 'replace')
    def rawDataOutFn(self, data):
        if (data == ' '): return 
        print 'SEND', unicode(data,'utf-8','ignore').encode('ascii', 'replace')
