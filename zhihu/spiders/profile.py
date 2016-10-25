# -*- coding=utf8 -*-
import os
import re
import time
import json
import logging
from PIL import Image

from urllib.parse import urlencode
from scrapy.spiders import CrawlSpider
from scrapy.selector import Selector
from scrapy.http import Request, FormRequest

from zhihu.items import ZhihuPeopleItem, ZhihuRelationItem
from zhihu.constants import Gender, People, HEADER


class ZhihuSipder(CrawlSpider):
    name = "zhihu"
    allowed_domains = ["www.zhihu.com"]
    start_url = "https://www.zhihu.com/people/luteng0601"

    def __init__(self, *args, **kwargs):
        super(ZhihuSipder, self).__init__(*args, **kwargs)
        self.xsrf = ''

    def start_requests(self):
        """
        登陆页面 获取_xrsf
        """
        yield Request(
            url="https://www.zhihu.com/#signin",
            headers=HEADER,
            meta={'cookiejar': 1},
            callback=self.post_login,
        )

    def post_login(self, response):
        """
        解析登陆页面，发送登陆表单
        """
        # 获取_xsrf值
        self.xsrf = response.xpath('//input[@name="_xsrf"]/@value').extract()[0]
        # 获取验证码地址
        captcha_url = 'http://www.zhihu.com/captcha.gif?r='+str(time.time()*1000)+'&type=login'
        # 准备下载验证码
        yield Request(
            url= captcha_url,
            headers= HEADER,
            meta= {
                'cookiejar':response.meta['cookiejar'],
                '_xsrf':self.xsrf
            },
            callback= self.download_captcha
        )

    def download_captcha(self, response):
        # 下载验证码
        with open('captcha.gif', 'wb') as f:
            f.write(response.body)
        # 用软件打开验证码图片
        im = Image.open('captcha.gif')
        im.show()
        # 输入验证码
        captcha = input('Please enter captcha:\n>')
        yield FormRequest(
            url= 'https://www.zhihu.com/login/email',
            method='POST',
            headers=HEADER,
            formdata={
                'email': '******',  #邮箱账号
                'password': '******', #密码
                '_xsrf': self.xsrf,
                'remember_me': 'true',
                'captcha': captcha
            },
            meta={
                'cookiejar': response.meta['cookiejar']
            },
            callback=self.after_login
        )

    def after_login(self, response):
        """
        登陆完成后从第一个用户开始爬数据
        """
        yield Request(
            url= self.start_url,
            meta= {'cookiejar': response.meta['cookiejar']},
            callback= self.parse_people,
            errback= self.parse_err,
        )

    def parse_people(self, response):
        """
        解析用户主页
        """
        selector = Selector(response)
        nickname=selector.xpath(
            '//div[@class="title-section"]/span[@class="name"]/text()'
        ).extract_first()
        zhihu_id=os.path.split(response.url)[-1]
        location=selector.xpath(
            '//span[@class="location item"]/@title'
        ).extract_first()
        business=selector.xpath(
            '//span[@class="business item"]/@title'
        ).extract_first()
        gender = selector.xpath(
            '//span[@class="item gender"]/i/@class'
        ).extract_first()
        if gender is not None:
            gender = Gender.FEMALE if u'female' in gender else Gender.MALE
        employment =selector.xpath(
            '//span[@class="employment item"]/@title'
        ).extract_first()
        position = selector.xpath(
            '//span[@class="position item"]/@title'
        ).extract_first()
        education = selector.xpath(
            '//span[@class="education item"]/@title'
        ).extract_first()
        followee_count, follower_count = tuple(selector.xpath(
            '//div[@class="zm-profile-side-following zg-clear"]//strong/text()'
        ).extract())
        followee_count, follower_count = int(followee_count), int(follower_count)
        follow_urls = selector.xpath(
            '//div[@class="zm-profile-side-following zg-clear"]/a[@class="item"]/@href'
        ).extract()
        for url in follow_urls:
            complete_url = 'https://{}{}'.format(self.allowed_domains[0], url)
            yield Request(
                        url= complete_url,
                        meta={'cookiejar': response.meta['cookiejar'],},
                        callback=self.parse_follow,
                        errback=self.parse_err)

        item = ZhihuPeopleItem(
            nickname=nickname,
            zhihu_id = zhihu_id,
            location=location,
            business=business,
            gender=gender,
            employment=employment,
            position=position,
            education=education,
            followee_count=followee_count,
            follower_count=follower_count,
        )
        yield item

    def parse_follow(self, response):
        """
        解析follow数据
        """
        selector = Selector(response)
        people_links = selector.xpath('//a[@class="zg-link author-link"]/@href').extract()
        people_info = selector.xpath(
            '//span[@class="zm-profile-section-name"]/text()').extract_first()
        people_param = selector.xpath(
            '//div[@class="zh-general-list clearfix"]/@data-init').extract_first()

        re_result = re.search(r'\d+', people_info) if people_info else None
        people_count = int(re_result.group()) if re_result else len(people_links)
        if not people_count:
            return

        people_param = json.loads(people_param)
        post_url = 'https://{}/node/{}'.format(
            self.allowed_domains[0], people_param['nodename'])

        # 去请求所有的用户数据
        start = 20
        while start < people_count:
            payload = {
                'method': 'next',
                '_xsrf': self.xsrf,
                'params': people_param['params']
            }
            payload['params']['offset'] = start
            payload['params'] = json.dumps(payload['params'])
            HEADER.update({'Referer': response.url})
            start += 20

            yield Request(post_url,
                          method='POST',
                          meta={'cookiejar': response.meta['cookiejar']},
                          headers=HEADER,
                          body=urlencode(payload),
                          priority=100,
                          callback=self.parse_post_follow)

        # 请求所有的人
        zhihu_ids = []
        for people_url in people_links:
            zhihu_ids.append(os.path.split(people_url)[-1])
            yield Request(people_url,
                          meta={'cookiejar': response.meta['cookiejar']},
                          callback=self.parse_people,
                          errback=self.parse_err)

        # 返回数据
        url, user_type = os.path.split(response.url)
        user_type = People.Follower if user_type == u'followers' else People.Followee
        item = ZhihuRelationItem(
            zhihu_id=os.path.split(url)[-1],
            user_type=user_type,
            user_list=zhihu_ids
        )
        yield item

    def parse_post_follow(self, response):
        """
        获取动态请求拿到的人员
        """
        body = json.loads(response.body.decode('utf-8'))
        people_divs = body.get('msg', [])

        # 请求所有的人
        zhihu_ids = []
        for div in people_divs:
            selector = Selector(text=div)
            link = selector.xpath('//a[@class="zg-link author-link"]/@href').extract_first()
            if not link:
                continue

            zhihu_ids.append(os.path.split(link)[-1])
            yield Request(link,
                          meta={'cookiejar': response.meta['cookiejar']},
                          callback=self.parse_people,
                          errback=self.parse_err)

        url, user_type = os.path.split(response.request.headers['Referer'])
        user_type = People.Follower if user_type == u'followers' else People.Followee
        zhihu_id = os.path.split(url)[-1].decode('utf-8')
        yield ZhihuRelationItem(
            zhihu_id=zhihu_id,
            user_type=user_type,
            user_list=zhihu_ids,
        )

    def parse_err(self, response):
        logging.ERROR('crawl {} failed'.format(response.url))
