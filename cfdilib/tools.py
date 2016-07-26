# -*- coding: utf-8 -*-
import logging
import boto3
import re
import time
import urllib2

from boto3.s3.transfer import S3Transfer
from botocore.client import ClientError
from tempfile import NamedTemporaryFile
from lxml import etree
from os.path import isfile
from functools import wraps
from urlparse import urlparse

_logger = logging.getLogger(__name__)


def retry(exception_to_check, tries=4, delay=3, back_off=2, logger=None):  # pragma: no cover  # noqa
    """Retry calling the decorated function using an exponential backoff.

    http://www.saltycrane.com/blog/2009/11/trying-out-retry-decorator-python/
    original from: http://wiki.python.org/moin/PythonDecoratorLibrary#Retry

    :param exception_to_check: the exception to check. may be a tuple of
        exceptions to check
    :type exception_to_check: Exception or tuple
    :param tries: number of times to try (not retry) before giving up
    :type tries: int
    :param delay: initial delay between retries in seconds
    :type delay: int
    :param back_off: back_off multiplier e.g. value of 2 will double the
        delay each retry
    :type back_off: int
    :param logger: logger to use. If None, print
    :type logger: logging.Logger instance
    """

    def deco_retry(f):

        @wraps(f)
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay
            while mtries > 1:
                try:
                    return f(*args, **kwargs)
                except exception_to_check, e:
                    msg = "%s, Retrying in %d seconds..." % (str(e),
                                                             mdelay)
                    if logger:
                        logger.warning(msg)
                    else:
                        print msg
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= back_off
            return f(*args, **kwargs)

        return f_retry  # true decorator

    return deco_retry


class Tools(object):
    """Set of tools to manipulate things that generally will be common between
    several ways to manage signed files and/or external connections.

    Basically if a method is not legally required but technically necessary
    that method will be here."""

    def __init__(self, domain='s3.vauxoo.com', profile_name='dev'):
        self.cached = {}
        self.domain = domain
        self.profile_name = profile_name

    def s3_url(self, url):
        return url.replace(urlparse(url).netloc, self.domain)

    def send_s3_xsd(self, url_xsd):  # pragma: no cover
        """This method will not be re-run always, only locally and when xsd
        are regenerated, read the test_008_force_s3_creation on test folder
        """

        if self.check_s3(self.domain, urlparse(url_xsd).path[1:]):
            return url_xsd

        response = urllib2.urlopen(url_xsd)
        content = response.read()
        cached = NamedTemporaryFile(delete=False)
        named = cached.name

        # Find all urls in the main xslt file.

        urls = re.findall(r'href=[\'"]?([^\'" >]+)', content)

        # mapping in the main file the url's

        for original_url in urls:
            content = content.replace(
                original_url, self.s3_url(original_url))

        with cached as cache:
            cache.write(content)

        created_url = self.cache_s3(url_xsd, named)
        print 'Created Url Ok!: %s' % created_url

        # Mapping all internal url in the file to s3 cached env.
        for original_url in urls:

            # Expecting 1 level of deepest links in xsd if more, refactor this.

            response = urllib2.urlopen(original_url)
            content = response.read()

            # Find all urls in the main xslt file.

            in_urls = re.findall(r'href=[\'"]?([^\'" >]+)', content)

            # mapping in the main file the url's

            for orig_url in in_urls:
                content = content.replace(
                    orig_url, self.s3_url(orig_url))

            cached = NamedTemporaryFile(delete=False)
            with cached as cache:
                cache.write(content)
            named = cached.name

            new_url = self.cache_s3(original_url, named)
            print 'Created Url Ok!: %s' % new_url

        return created_url

    def check_s3(self, bucket, element):
        session = boto3.Session(profile_name=self.profile_name)
        s3 = session.resource('s3')

        try:
            s3.meta.client.head_bucket(Bucket=bucket)
        except ClientError:
            # If the bucket does not exists then simply use the original
            # I silently fail returning everything as it is in the url
            return False
        try:
            # If the key does not exists do not return False, but try to
            # create a readonly user in order to not have problems into the
            # travis environment.
            s3.Object(bucket, element).load()
        except ClientError:
            return False
        else:
            return True

    def cache_s3(self, url, named):
        """This will make a copy of the cache into amazon S3 Domain passed as
        parameter, if no domain then this will return the same url and the
        original file will be the one used.

        The ACL related to this will be the global ones configured into the
        environment, if not ACL this will raise a NoS3Credentials.

        Basically this is not to deploy automatically this is to be run once
        all is properly defined to catch the xsd in your own S3 instance and
        avoid third party (like government servers) failure, trying to manage
        the cache transparently to the user.

        This method was created due to SAT is too unstable to serve the files
        related with xsd and invoices, and this probably is the same in other
        governmental institutions.

        technical Note: Even if the tries are looking nested, this is perfect
        valid for this case https://docs.python.org/3/glossary.html#term-eafp.

        :param url: Element path to be cached.
        :type url: str
        :param named: Local path with the file already downloaded.
        :type named: str

        :return:
        """

        url_parsed = urlparse(url)
        if self.domain == urlparse(url).netloc:
            # If I am asking for the same domain it is probably because it
            # exists BTW
            return url

        # Get the service resource
        session = boto3.Session(profile_name=self.profile_name)
        s3 = session.resource('s3')
        client = session.client('s3')
        element = url_parsed.path[1:]

        # Get the bucket object (I am not connected yet this is setting a
        # local object in memory only)
        bucket = s3.Bucket(self.domain)

        # Dear future me there MUST be a more elegant way to do this.
        new_url = 'http://%s/%s' % (self.domain, element)

        if self.check_s3(bucket.name, element):
            return new_url

        # Look a way this code is not tested in coverage because this MUST run
        # locally due to s3 credentials.
        try:  # pragma: no cover
            # No coverage code unreachable on travis 'intentionally'
            transfer = S3Transfer(client)
            # Making the object public
            transfer.upload_file(named, bucket.name, element,
                                 extra_args={'ACL': 'public-read'})
        except ClientError as inst:  # pragma: no cover
            # No coverage code unreachable on travis 'intentionally'
            print inst.message
            return url
        else:  # pragma: no cover
            # No coverage code unreachable on travis 'intentionally'
            return new_url

    @staticmethod
    def is_url(element_name):
        return element_name.startswith('http')

    @retry(urllib2.URLError, tries=3, delay=3, back_off=2)
    def cache_it(self, url):
        """Take an url which deliver a plain document  and convert it to a
        temporary file, this document is an xslt file expecting contains all
        xslt definitions, then the cache process is recursive.

        :param url: document origin url
        :type url: str

        :return file_path: local new absolute path
        :rtype file_path: str
        """
        def cache_it(_url):
            cached = NamedTemporaryFile(delete=False)
            named = cached.name
            _response = urllib2.urlopen(_url)
            _content = _response.read()
            with cached as cache:
                cache.write(_content)
            self.cached.update({_url: named})
            return _content, named

        # If on this runtime it is cached do not cache it again.
        if self.cached.get(url) and isfile(self.cached[url]):
            return self.cached[url]

        internal = 0

        # TODO: unwire this to xslt, but usefull for now
        if url.endswith('xslt') and not internal:

            # check

            # Opening the first file just the first time.
            response = urllib2.urlopen(url)
            content = response.read()

            # Find all urls in the main xslt file.

            urls = re.findall(r'href=[\'"]?([^\'" >]+)', content)

            # Mapping all internal url in the file to local cached files.

            for original_url in urls:
                internal += 1
                content, _file = cache_it(original_url)
                content.replace(original_url, _file)
        return cache_it(url)[1]

    @staticmethod
    def get_original(document, xslt):
        """Get the original chain given document path and xslt local path

        :param str document: local absolute path to document
        :param str xslt: local absolute path to xst file
        :return: new chain generated.
        :rtype: str
        """
        dom = etree.parse(document)  # TODO: cuando este probando -
        # fuente:
        # http://stackoverflow.com/questions/16698935/how-to-transform-an-xml-file-using-xslt-in-python
        xslt = etree.parse(xslt)
        transform = etree.XSLT(xslt)
        newdom = transform(dom)
        return newdom


tools = Tools()
