#!/usr/bin/env python

import sys
import time
import re
import numpy
import argparse
import urllib2
from socket import timeout

from Queue import Queue, Empty
from threading import Thread

LOG_LINE_REGEX = r'([0-9\.]+)\s-\s-\s\[.*?\]\s"GET\s(.*?)\sHTTP.*?"\s[0-9]+\s[0-9]+\s".*?"\s"(.*?)"'

class LogParser(object):

	QUEUE_SIZE_MAX = 1000

	def __init__(self, log_queue, log_file, limit):
		self.log_file = log_file
		self.queue = log_queue
		self.log_regex = re.compile(LOG_LINE_REGEX)
		self.limit = limit
		self.running = False
		self.queued = 0

	def _get_parsed_line(self, line):
		m = self.log_regex.match(line)
		if not m:
			return None
		return m.groups()

	def _parse_next_batch(self):
		lines = self.log_file.readlines(1000000)

		if not lines:
			return False

		for line in lines:
			if self.limit > 0 and self.queued >= self.limit:
				return False

			while self.queue.qsize() > self.QUEUE_SIZE_MAX:
				time.sleep(0.1)

			parsed_line = self._get_parsed_line(line)
			if parsed_line:
				self.queue.put(parsed_line)
				self.queued += 1
		return True

	def _parser_job(self):
		try:
			while self._parse_next_batch(): pass
		except Exception, e:
			print 'Log parser exception: %s' % e
		self.running = False

	def start(self):
		self.running = True

		self.parser_job = Thread(target=self._parser_job)
		self.parser_job.daemon=True
		self.parser_job.start()

	def join(self):
		while self.parser_job.isAlive():
			self.parser_job.join(1)


class RequestWorker(object):
	def __init__(self, log_file, address, timeout, limit, workers):
		self.address = address
		self.timeout = timeout

		self.queue = Queue()

		self.limit = limit
		self.workers = workers

		self.log_parser = LogParser(self.queue, log_file, limit)

		self.print_on = max(int(limit / 10), 1000)

		self.results = {
			'total': 0,
			'error': 0,
			'ok': 0,
		}
		self.times = []

	def _print_progress(self):
		if self.results['total'] % self.print_on == 0:
			time_total = time.time() - self.t0
			print 'done', self.results['total'], '/', self.limit if self.limit > 0 else '?', '|', int(round(self.results['ok'] / time_total)), 'per sec'

	def _make_request(self):
		try:
			ip, path, user_agent = self.queue.get(block=self.log_parser.running, timeout=1)
		except Empty:
			return False

		url = self.address + path

		try:
			tr0 = time.time()

			request = urllib2.Request(url, headers={"X-RealIP": ip, "User-Agent": user_agent})
			response = urllib2.urlopen(request, timeout=self.timeout)

			if response.getcode() >= 400:
				raise urllib2.URLError("Response code error %s" % response.getcode())

			response.read()

			self.times.append(time.time() - tr0)

			self.results['ok'] += 1
		except (urllib2.URLError, timeout), e:
			print '%s < %s' % (e, url)
			self.results['error'] += 1

		self.results['total'] += 1

		self._print_progress()

		return True

	def _start(self):
		self.log_parser.start()

		self.jobs = []
		for i in xrange(self.workers):
			co = Thread(target=self._log_consumer_job)
			co.daemon=True
			self.jobs.append(co)
			co.start()

		self.t0 = time.time()

		self.log_parser.join()

	def _join(self):
		for co in self.jobs:
			while co.isAlive():
				co.join(1)

		self.time_total = time.time() - self.t0

	def run(self):
		self._start()
		self._join()

	def _log_consumer_job(self):
		try:
			while self._make_request(): pass
		except Exception, e:
			print 'Log consumer exception: %s' % e

	def print_report(self):
		#TODO: rewrite this method
		print

		print 'requests %s' % self.results['total']
		print 'ok       %s' % self.results['ok']
		print 'error    %s' % self.results['error']
		print

		print 'Total time: %s sec' % round(self.time_total, 2)
		print 'Requests per second: %s' % round(self.results['ok'] / self.time_total)
		print

		def get_ms(f):
			return round(f, 4) * 1000

		print 'Response times:'
		print 'mean:\t%sms' % get_ms(numpy.mean(self.times))

		ts = sorted(self.times)
		tlen = len(self.times)
		for p in xrange(10, 100, 10):
			c = float(p) / 100
			print '%d%%\t%sms' % (c * 100, get_ms(ts[int(tlen * c)]))
		print '100%%\t%sms' % get_ms(ts[-1])


def parse_args():
	parser = argparse.ArgumentParser(description='Replay HTTP Benchmark.')

	parser.add_argument('-a', '--address', type=str, help='HTTP server address', required=True)
	parser.add_argument('-f', '--file', type=str, help='Log file location', required=True)
	parser.add_argument('-c', '--concurrency', type=int, default=1, help='Number of concurrent requests')
	parser.add_argument('-r', '--requests', type=int, default=-1, help='Number of requests')
	parser.add_argument('-t', '--timeout', type=int, default=1, help='Request timeout in seconds')

	return parser.parse_args(sys.argv[1:])

def run_benchmark(options):
	log_file = open(options['file'], 'r')

	request_worker = RequestWorker(log_file, options['address'], options['timeout'],
			options['requests'], options['concurrency'])

	request_worker.run()
	request_worker.print_report()


if __name__ == '__main__':
	options = parse_args()
	run_benchmark(options.__dict__)
