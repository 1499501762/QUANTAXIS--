# coding:utf-8
#
# The MIT License (MIT)
#
# Copyright (c) 2016-2021 yutiansut/QUANTAXIS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import copy
import datetime
import warnings

import numpy as np
import pandas as pd
from pymongo import DESCENDING, ASCENDING

from QUANTAXIS import __version__
from QUANTAXIS.QAARP.market_preset import MARKET_PRESET
from QUANTAXIS.QAEngine.QAEvent import QA_Worker
from QUANTAXIS.QAMarket.QAOrder import QA_Order, QA_OrderQueue
from QUANTAXIS.QAMarket.QAPosition import QA_Position
from QUANTAXIS.QASU.save_account import save_account, update_account
from QUANTAXIS.QAUtil.QASetting import DATABASE
from QUANTAXIS.QAUtil.QADate_trade import (
    QA_util_if_trade,
    QA_util_get_next_day,
    QA_util_get_trade_range
)
from QUANTAXIS.QAUtil.QAParameter import (
    ACCOUNT_EVENT,
    AMOUNT_MODEL,
    BROKER_TYPE,
    ENGINE_EVENT,
    FREQUENCE,
    MARKET_TYPE,
    ORDER_DIRECTION,
    ORDER_MODEL,
    RUNNING_ENVIRONMENT,
    TRADE_STATUS,
    EXCHANGE_ID
)
from QUANTAXIS.QAUtil.QARandom import QA_util_random_with_topic

# 2017/6/4??????: ????????????????????????????????????


# pylint: disable=old-style-class, too-few-public-methods
class QA_AccountPRO(QA_Worker):
    def __init__(
        self,
        user_cookie: str,
        portfolio_cookie: str,
        account_cookie=None,
        strategy_name=None,
        market_type=MARKET_TYPE.STOCK_CN,
        frequence=FREQUENCE.DAY,
        broker=BROKER_TYPE.BACKETEST,
        init_hold={},
        init_cash=1000000,
        commission_coeff=0.00025,
        tax_coeff=0.001,
        margin_level={},
        allow_t0=False,
        allow_sellopen=False,
        allow_margin=False,
        running_environment=RUNNING_ENVIRONMENT.BACKETEST,
        auto_reload=False,
        generated='direct',
        start=None,
        end=None
    ):
        super().__init__()

        self._history_headers = [
            'datetime',  # ??????/??????
            'code',  # ??????
            'price',  # ?????????
            'amount',  # ????????????(?????? ??????  ?????? ??????)
            'cash',  # ??????
            'order_id',  # ???????????????
            'realorder_id',  # ??????????????????
            'trade_id',  # ????????????
            'account_cookie',  # ??????id
            'commission',  # ?????????
            'tax',  # ???
            'message',  # ??????
            'frozen',  # ????????????.
            'direction',  # ??????,
            'total_frozen'
        ]
        self.activity = {}
        ########################################################################
        # ?????????:

        if user_cookie is None or portfolio_cookie is None:
            raise RuntimeError('QUANTAXIS 1.3.0??????: ???????????????Account????????????????????????/?????????')
        self.user_cookie = user_cookie
        self.strategy_name = strategy_name
        self.portfolio_cookie = portfolio_cookie
        self.account_cookie = QA_util_random_with_topic(
            'AccPro'
        ) if account_cookie is None else account_cookie
        self.market_type = market_type
        self.broker = broker
        self.frequence = frequence
        self.running_environment = running_environment
        ########################################################################
        self._market_data = None
        self._currenttime = None
        self.commission_coeff = commission_coeff
        self.tax_coeff = tax_coeff
        self.datetime = None
        self.running_time = datetime.datetime.now()
        self.quantaxis_version = __version__
        self.client = DATABASE.account
        self.start_ = start
        self.end_ = end
        ### ????????????????????????index??????, ???????????????????????????????????????????????????
        self.client.create_index(
            [
                ("account_cookie",
                 ASCENDING),
                ("user_cookie",
                 ASCENDING),
                ("portfolio_cookie",
                 ASCENDING)
            ],
            unique=True
        )
        ########################################################################
        # ?????????
        self.orders = QA_OrderQueue()  # ???????????????
        self.init_cash = init_cash
        self.init_hold = pd.Series(
            init_hold,
            name='amount'
        ) if isinstance(init_hold,
                        dict) else init_hold
        self.init_hold.index.name = 'code'
        self.positions = {}
        if len(self.init_hold) > 0:
            for code in init_hold.keys():
                self.positions[code] = QA_Position(code=code, user_cookie=self.user_cookie,
                                                   volume_long_his=init_hold[code],
                                                   portfolio_cookie=self.portfolio_cookie,
                                                   account_cookie=self.account_cookie,
                                                   auto_reload=False)
        self.cash = [self.init_cash]
        self.cash_available = self.cash[-1]  # ????????????
        self.sell_available = copy.deepcopy(self.init_hold)
        self.buy_available = copy.deepcopy(self.init_hold)
        self.history = []
        self.time_index_max = []

        # ????????????, ?????????????????????
        # ???????????????, ??????????????????/????????????????????????????????????
        self.static_balance = {
            'static_assets': [],
            'cash': [],
            'frozen': [],
            'hold': [],
            'date': []
        }                        # ?????????
        self.today_trade = {'last': [], 'current': []}
        self.today_orders = {'last': [], 'current': []}

        ########################################################################
        # ?????????
        # 1.????????????t+0 ??????????????????
        # 2.????????????????????????
        # 3.???????????????????????????/ ????????????false ??????????????????????????????(dict??????)

        # ??????: allow_t0 True allow_sellopen True
        #

        self.allow_t0 = allow_t0
        self.allow_sellopen = allow_sellopen
        self.allow_margin = allow_margin
        self.margin_level = margin_level  # ???????????????

        if self.market_type is MARKET_TYPE.FUTURE_CN:
            self.allow_t0 = True
            self.allow_sellopen = True
            self.allow_margin = True

        self.market_preset = MARKET_PRESET()
        # if self.allow_t0 and self.allow_sellopen or self.market_type is MARKET_TYPE.FUTURE_CN:
        #     self.load_marketpreset()
        """???????????????/?????? ==> ????????????????????????  frozen

        ?????????????????????, ???????????????

        1. frozen  ?????????dict :   {[code]:queue}
            key????????? value??????????????????queue

        """

        self.frozen = {}  # ????????????(?????????)
        self.finishedOrderid = []

        if auto_reload:
            self.reload()

        print(self.positions)

    def __repr__(self):
        return '< QA_AccountPRO {} market: {}>'.format(
            self.account_cookie,
            self.market_type
        )

    def get_position(self, code: str) -> QA_Position:
        """Get

        ??????position
        same apis with QIFIAccount

        return  <QA_Position>

        """

        pos = self.positions.get(code, QA_Position(code=code, user_cookie=self.user_cookie,
                                                   portfolio_cookie=self.portfolio_cookie,
                                                   account_cookie=self.account_cookie,
                                                   auto_reload=False))
        if pos.market_type == self.market_type:
            self.positions[code] = pos
            return pos
        else:
            print('Current AccountPro {} is {} doesnot support {}'.format(
                self.account_cookie, self.market_type, pos.market_type))

    @property
    def hold_available(self):
        pass

    @property
    def message(self):
        'the standard message which can be transfer'
        return {
            'source':
            'account',
            'frequence':
            self.frequence,
            'account_cookie':
            self.account_cookie,
            'portfolio_cookie':
            self.portfolio_cookie,
            'user_cookie':
            self.user_cookie,
            'broker':
            self.broker,
            'market_type':
            self.market_type,
            'strategy_name':
            self.strategy_name,
            'current_time':
            str(self._currenttime),
            'allow_sellopen':
            self.allow_sellopen,
            'allow_margin':
            self.allow_margin,
            'allow_t0':
            self.allow_t0,
            'margin_level':
            self.margin_level,
            'init_assets':
            self.init_assets,
            'init_cash':
            self.init_cash,
            'init_hold':
            self.init_hold.to_dict(),
            'commission_coeff':
            self.commission_coeff,
            'tax_coeff':
            self.tax_coeff,
            'cash':
            self.cash,
            'history':
            self.history,
            'trade_index':
            self.time_index_max,
            'running_time':
            str(datetime.datetime.now())
            if self.running_time is None else str(self.running_time),
            'quantaxis_version':
            self.quantaxis_version,
            'running_environment':
            self.running_environment,
            'start_date':
            self.start_date,
            'end_date':
            self.end_date,
            'frozen':
            self.frozen,
            'finished_id':
            self.finishedOrderid
        }

    @property
    def freecash_precent(self):
        """??????????????????

        Returns:
            float
        """

        return self.cash_available / self.init_cash

    def load_marketpreset(self):
        """???????????????
        """

        self.market_preset = MARKET_PRESET()

    @property
    def init_hold_with_account(self):
        """???account_cookie??????????????????

        Returns:
            [type] -- [description]
        """

        return self.init_hold.reset_index().assign(
            account_cookie=self.account_cookie
        ).set_index(['code',
                     'account_cookie'])

    @property
    def init_assets(self):
        """?????????????????????

        Returns:
            dict -- 2keys-cash,hold
        """

        return {'cash': self.init_cash, 'hold': self.init_hold.to_dict()}

    @property
    def code(self):
        """
        ???????????????????????? ???set ??????
        """
        return list(set([item[1] for item in self.history]))

    @property
    def date(self):
        """?????????????????????

        Arguments:
            self {[type]} -- [description]

        Returns:
            [type] -- [description]
        """

        if self.datetime is not None:
            return str(self.datetime)[0:10]
        else:
            return None

    @property
    def start_date(self):
        """???????????????????????????(?????????????????????)

        Raises:
            RuntimeWarning -- [description]

        Returns:
            [type] -- [description]
        """
        if self.start_ == None:
            if len(self.time_index_max) > 0:
                return str(min(self.time_index_max))[0:10]
            else:
                print(
                    RuntimeWarning(
                        'QAACCOUNT: THIS ACCOUNT DOESNOT HAVE ANY TRADE'
                    )
                )
        else:
            return self.start_

    @property
    def end_date(self):
        """???????????????????????????(?????????????????????)

        Raises:
            RuntimeWarning -- [description]

        Returns:
            [type] -- [description]
        """
        if self.end_ == None:
            if len(self.time_index_max) > 0:
                return str(max(self.time_index_max))[0:10]
            else:
                print(
                    RuntimeWarning(
                        'QAACCOUNT: THIS ACCOUNT DOESNOT HAVE ANY TRADE'
                    )
                )
        else:
            return self.end_


    def set_end_date(self, date):
        if QA_util_if_trade(date):
            self.end_ = date
        else:
            print('error {} not a trade date'.format(date))

    @property
    def market_data(self):
        return self._market_data

    @property
    def trade_range(self):
        return QA_util_get_trade_range(self.start_date, self.end_date)

    @property
    def trade_range_max(self):
        if self.start_date < str(min(self.time_index_max))[0:10]:
            return QA_util_get_trade_range(self.start_date, self.end_date)
        else:

            return QA_util_get_trade_range(str(min(self.time_index_max))[0:10],
                                           str(max(str(max(self.time_index_max)), self.end_date))[0:10])

    @property
    def total_commission(self):
        """
        ????????????
        """
        try:
            return np.asarray(self.history).T[9].sum()
        except:
            return 0

    @property
    def total_tax(self):
        """
        ????????????
        """
        try:
            return np.asarray(self.history).T[10].sum()
        except:
            return 0

    @property
    def time_index(self):
        if len(self.time_index_max):
            res_ = pd.DataFrame(self.time_index_max)
            res_.columns = (['datetime'])
            res_['date'] = [i[0:10] for i in res_['datetime']]
            res_ = res_[res_['date'].isin(self.trade_range)]
            return list(res_['datetime'])
        else:
            return self.time_index_max
#
#        if self.start_date < str(min(self.time_index))[0:10] :
#             return QA_util_get_trade_range(self.start_date, self.end_date)
#        else:
#            return QA_util_get_trade_range(str(min(self.time_index))[0:10], str(max(self.time_index))[0:10])

    @property
    def history_min(self):
        if len(self.history):
            res_ = pd.DataFrame(self.history)
            res_['date'] = [i[0:10] for i in res_[0]]
            res_ = res_[res_['date'].isin(self.trade_range)]
            return np.array(res_.drop(['date'], axis=1)).tolist()
        else:
            return self.history

    @property
    def history_table_min(self):
        '?????????????????????table'
        if len(self.history_min) > 0:
            lens = len(self.history_min[0])
        else:
            lens = len(self._history_headers)

        return pd.DataFrame(
            data=self.history_min,
            columns=self._history_headers[:lens]
        ).sort_index()


#    @property
#    def history(self):
#        if len(self.history_max):
#            res_=pd.DataFrame(self.history_max)
#            res_['date']=[ i[0:10]  for i in res_[0]]
#            res_=res_[res_['date'].isin(self.trade_range)]
#            return np.array(res_.drop(['date'],axis=1)).tolist()
#        else:
#            return self.history_max
#        res_=pd.DataFrame(self.time_index_max)
#        res_.columns=(['datetime'])
#        res_['date']=[ i[0:10]  for i in res_['datetime']]
#        res_=res_[res_['date'].isin(self.trade_range)]

    @property
    def trade_day(self):
        return list(
            pd.Series(self.time_index_max
                      ).apply(lambda x: str(x)[0:10]).unique()
        )

    @property
    def history_table(self):
        '???????????????table'
        if len(self.history) > 0:
            lens = len(self.history[0])
        else:
            lens = len(self._history_headers)

        return pd.DataFrame(
            data=self.history,
            columns=self._history_headers[:lens]
        ).sort_index()

    @property
    def today_trade_table(self):
        return pd.DataFrame(
            data=self.today_trade['current'],
            columns=self._history_headers
        ).sort_index()

    @property
    def cash_table(self):
        '?????????table'
        _cash = pd.DataFrame(
            data=[self.cash[1::],
                  self.time_index_max],
            index=['cash',
                   'datetime']
        ).T
        _cash = _cash.assign(
            date=_cash.datetime.apply(lambda x: pd.to_datetime(str(x)[0:10]))
        ).assign(account_cookie=self.account_cookie)                          # .sort_values('datetime')
        return _cash.set_index(['datetime', 'account_cookie'], drop=False)
        """
        ????????????
        @2018-06-09

        # ???????????????????????????

        1. ????????????hold:

        ????????????/TZero??????:
            hold = ????????????(init_hold)+ ??????????????????????????????????????????????????????(hold_available)

        ????????????(???????????????????????????)hold_available:
            self.history ????????????

        2. ?????????????????????(sell_available)

        ????????????:
            sell_available
                ?????????: init_hold+ ????????????(???-)
                ?????????: init_hold+ ????????????(???+ ???-)
        TZero??????:
            sell_available
                ?????????: init_hold - ???????????????????????????(abs(???+ ???-))
                ???????????? ???????????????(?????????hold={})
                ?????????: init_hold
        """

    @property
    def hold(self):
        """????????????
        """
        return pd.concat(
            [self.init_hold,
             self.hold_available]
        ).groupby('code').sum().replace(0,
                                        np.nan).dropna().sort_index()

    @property
    def hold_available(self):
        """????????????
        """
        return self.history_table.groupby('code').amount.sum().replace(
            0,
            np.nan
        ).dropna().sort_index()

    # @property
    # def order_table(self):
    #     """return order trade list"""
    #     return self.orders.trade_list

    @property
    def trade(self):
        """???????????????pivot???

        Returns:
            pd.DataFrame

            ?????????pivot_table????????????np.sum
        """

        return self.history_table.pivot_table(
            index=['datetime',
                   'account_cookie'],
            columns='code',
            values='amount',
            aggfunc=np.sum
        ).fillna(0).sort_index()

    @property
    def daily_cash(self):
        '?????????????????????????????????'
        res = self.cash_table.drop_duplicates(subset='date', keep='last')
        le = pd.DataFrame(
            pd.Series(
                data=None,
                index=pd.to_datetime(self.trade_range_max).set_names('date'),
                name='predrop'
            )
        )
        ri = res.set_index('date')
        res_ = pd.merge(le, ri, how='left', left_index=True, right_index=True)
        res_ = res_.ffill().fillna(
            self.init_cash
        ).drop(['predrop',
                'datetime',
                'account_cookie'],
               axis=1).reset_index().set_index(['date'],
                                               drop=False).sort_index()
        res_ = res_[res_.index.isin(self.trade_range)]
        return res_

    @property
    def daily_hold(self):
        '?????????????????????????????????'
        data = self.trade.cumsum()
        if len(data) < 1:
            return None
        else:
            # print(data.index.levels[0])
            data = data.assign(account_cookie=self.account_cookie).assign(
                date=pd.to_datetime(data.index.levels[0]).date
            )

            data.date = pd.to_datetime(data.date)
            data = data.set_index(['date', 'account_cookie'])
            res = data[~data.index.duplicated(keep='last')].sort_index()
            # ??????????????????????????????????????????????????? ????????????market_value??????????????????
            le = pd.DataFrame(
                pd.Series(
                    data=None,
                    index=pd.to_datetime(self.trade_range_max
                                         ).set_names('date'),
                    name='predrop'
                )
            )
            ri = res.reset_index().set_index('date')
            res_ = pd.merge(
                le,
                ri,
                how='left',
                left_index=True,
                right_index=True
            )
            res_ = res_.ffill().fillna(0).drop(
                ['predrop',
                 'account_cookie'],
                axis=1
            ).reset_index().set_index(['date']).sort_index()
            res_ = res_[res_.index.isin(self.trade_range)]
            return res_

    @property
    def daily_frozen(self):
        '?????????????????????????????????'
        res_ = self.history_table.assign(
            date=pd.to_datetime(self.history_table.datetime)
        ).set_index('date').resample('D').total_frozen.last().fillna(method='pad')
        res_ = res_[res_.index.isin(self.trade_range)]
        return res_

    @property
    def latest_cash(self):
        'return the lastest cash ????????????'
        return self.cash[-1]

    @property
    def current_time(self):
        'return current time (in backtest/real environment)'
        return self._currenttime

    def hold_table(self, datetime=None):
        "??????????????????????????? ?????????????????????,?????????????????????????????????"
        if datetime is None:
            hold_available = self.history_table.set_index(
                'datetime'
            ).sort_index().groupby('code').amount.sum().sort_index()
        else:
            hold_available = self.history_table.set_index(
                'datetime'
            ).sort_index().loc[:datetime].groupby('code'
                                                  ).amount.sum().sort_index()

        return pd.concat([self.init_hold,
                          hold_available]).groupby('code').sum().sort_index(
        ).apply(lambda x: x if x > 0 else None).dropna()

    def current_hold_price(self):
        """???????????????????????????  ??????????????????????????????

        Returns:
            [type] -- [description]
        """

        def weights(x):
            n = len(x)
            res = 1
            while res > 0 or res < 0:
                res = sum(x[:n]['amount'])
                n = n - 1

            x = x[n + 1:]

            if sum(x['amount']) != 0:
                return np.average(
                    x['price'],
                    weights=x['amount'],
                    returned=True
                )
            else:
                return np.nan

        return self.history_table.set_index(
            'datetime',
            drop=False
        ).sort_index().groupby('code').apply(weights).dropna()

    def hold_price(self, datetime=None):
        """??????????????????  ?????????????????????,?????????????????????????????????

        Keyword Arguments:
            datetime {[type]} -- [description] (default: {None})

        Returns:
            [type] -- [description]
        """

        def weights(x):
            if sum(x['amount']) != 0:
                return np.average(
                    x['price'],
                    weights=x['amount'],
                    returned=True
                )
            else:
                return np.nan

        if datetime is None:
            return self.history_table.set_index(
                'datetime',
                drop=False
            ).sort_index().groupby('code').apply(weights).dropna()
        else:
            return self.history_table.set_index(
                'datetime',
                drop=False
            ).sort_index().loc[:datetime].groupby('code').apply(weights
                                                                ).dropna()

    # @property
    def hold_time(self, datetime=None):
        """????????????

        Keyword Arguments:
            datetime {[type]} -- [description] (default: {None})
        """

        def weights(x):
            if sum(x['amount']) != 0:
                return pd.Timestamp(self.datetime
                                    ) - pd.to_datetime(x.datetime.max())
            else:
                return np.nan

        if datetime is None:
            return self.history_table.set_index(
                'datetime',
                drop=False
            ).sort_index().groupby('code').apply(weights).dropna()
        else:
            return self.history_table.set_index(
                'datetime',
                drop=False
            ).sort_index().loc[:datetime].groupby('code').apply(weights
                                                                ).dropna()

    def reset_assets(self, init_cash=None):
        'reset_history/cash/'
        self.sell_available = copy.deepcopy(self.init_hold)
        self.history = []
        self.init_cash = init_cash
        self.cash = [self.init_cash]
        self.cash_available = self.cash[-1]  # ????????????

    @property
    def close_positions_order(self):
        """?????????

        Raises:
            RuntimeError -- if ACCOUNT.RUNNING_ENVIRONMENT is NOT TZERO

        Returns:
            list -- list with order
        """

        order_list = []
        time = '{} 15:00:00'.format(self.date)
        if self.running_environment == RUNNING_ENVIRONMENT.TZERO:
            for code, amount in self.hold_available.iteritems():
                order = False
                if amount < 0:
                    # ?????????????????? ??????
                    order = self.send_order(
                        code=code,
                        price=0,
                        amount=abs(amount),
                        time=time,
                        towards=ORDER_DIRECTION.BUY,
                        order_model=ORDER_MODEL.CLOSE,
                        amount_model=AMOUNT_MODEL.BY_AMOUNT,
                    )
                elif amount > 0:
                    # ??????????????????, ??????
                    order = self.send_order(
                        code=code,
                        price=0,
                        amount=abs(amount),
                        time=time,
                        towards=ORDER_DIRECTION.SELL,
                        order_model=ORDER_MODEL.CLOSE,
                        amount_model=AMOUNT_MODEL.BY_AMOUNT
                    )
                if order:
                    order_list.append(order)
            return order_list
        else:
            raise RuntimeError(
                'QAACCOUNT with {} environments cannot use this methods'.format(
                    self.running_environment
                )
            )

    def send_order(
            self,
            code=None,
            amount=None,
            time=None,
            towards=None,
            price=None,
            money=None,
            order_model=ORDER_MODEL.LIMIT,
            amount_model=AMOUNT_MODEL.BY_AMOUNT,
            order_id=None,
            position_id=None,
            *args,
            **kwargs
    ):

        wrong_reason = None
        assert code is not None and time is not None and towards is not None and order_model is not None and amount_model is not None
        date = str(time)[0:10] if len(str(time)) == 19 else str(time)
        time = str(time) if len(str(time)) == 19 else '{} 09:31:00'.format(
            str(time)[0:10]
        )
        if self.allow_margin:
            amount = amount if amount_model is AMOUNT_MODEL.BY_AMOUNT else int(
                money / (
                    self.market_preset.get_unit(code) *
                    self.market_preset.get_frozen(code) * price *
                    (1 + self.commission_coeff)
                ) / 100
            ) * 100
        else:
            amount = amount if amount_model is AMOUNT_MODEL.BY_AMOUNT else int(
                money / (price * (1 + self.commission_coeff)) / 100
            ) * 100

        # ????todo ??????Utils?????????  money_to_amount ??????????????????
        if self.allow_margin:
            money = amount * price * self.market_preset.get_unit(code)*self.market_preset.get_frozen(code) * \
                (1+self.commission_coeff) if amount_model is AMOUNT_MODEL.BY_AMOUNT else money
        else:
            print(amount)
            print(price)
            money = amount * price * \
                (1+self.commission_coeff) if amount_model is AMOUNT_MODEL.BY_AMOUNT else money

        # flag ???????????? ?????????????????????????????????????????????
        flag = False

        assert (int(towards) != 0)
        if int(towards) in [1, 2, 3]:
            # ??????????????????(????????????.??????.??????)
            if self.cash_available >= money:
                if self.market_type == MARKET_TYPE.STOCK_CN:  # ??????????????? ??????????????????100??????????????????
                    amount = int(amount / 100) * 100
                    self.cash_available -= money
                    flag = True

                if self.running_environment == RUNNING_ENVIRONMENT.TZERO:

                    if abs(self.buy_available.get(code, 0)) >= amount:
                        flag = True
                        self.cash_available -= money
                        self.buy_available[code] -= amount
                    else:
                        flag = False
                        wrong_reason = 'T0????????????????????????'

                if self.market_type == MARKET_TYPE.FUTURE_CN:
                    # ??????????????????-- ?????????????????????
                    if towards == 3:  # ??????
                        pos = self.get_position(code)
                        # ??????????????????:
                        # amount??????????????? ???  ????????????-3??? ?????????1???

                        #left_amount = amount+_hold if _hold < 0 else amount
                        money_need = abs(
                            float(amount * price * (1 + self.commission_coeff))
                        )

                        if self.cash_available >= money_need:
                            if pos.volume_short > 0:
                                self.cash_available -= money_need

                                flag = True
                            else:
                                wrong_reason = '??????????????????'
                        else:
                            wrong_reason = '????????????????????????'
                    if towards == 2:
                        self.cash_available -= money
                        flag = True
            else:
                wrong_reason = 'QAACCOUNT: ?????????????????? cash_available {}  code {} time {} amount {} towards {}'.format(
                    self.cash_available,
                    code,
                    time,
                    amount,
                    towards
                )
        elif int(towards) in [-1, -2, -3]:
            # ??????????????????(???????????????????????????allow_sellopen????????????. ????????????)
            # print(self.sell_available[code])
            pos = self.get_position(code)  # _hold ???????????????

            # ????????????hold> amount>0
            # ????????????>????????????

            if towards == -1:
                if pos.volume_long_his >= amount:
                    self.sell_available[code] -= amount
                    # towards = ORDER_DIRECTION.SELL
                    flag = True
            elif towards == -2:
                if self.allow_sellopen:
                    if self.cash_available >= money:  # ?????????????????????????????????????????????????????? ??????????????????
                                                    # self.cash_available -= money
                        flag = True
                    else:
                        print('sellavailable', _hold)
                        print('amount', amount)
                        print('aqureMoney', money)
                        print('cash', self.cash_available)
                        wrong_reason = "??????????????????"
                else:
                    wrong_reason = "???????????????"

            else:
                if pos.volume_long >= amount:
                    self.sell_available[code] -= amount
                    # towards = ORDER_DIRECTION.SELL
                    flag = True
                # ??????????????????<????????????
                else:
                    wrong_reason = "??????????????????"

        if flag and (amount > 0):
            _order = QA_Order(
                user_cookie=self.user_cookie,
                strategy=self.strategy_name,
                frequence=self.frequence,
                account_cookie=self.account_cookie,
                code=code,
                market_type=self.market_type,
                date=date,
                datetime=time,
                sending_time=time,
                callback=self.receive_deal,
                amount=amount,
                price=price,
                order_model=order_model,
                towards=towards,
                money=money,
                broker=self.broker,
                amount_model=amount_model,
                commission_coeff=self.commission_coeff,
                tax_coeff=self.tax_coeff,
                position_id=position_id,
                order_id=order_id,
                *args,
                **kwargs
            )                                                           # init
            # ????????????order??????????????? ????????? QA_Order ?????????????????????
            self.datetime = time
            self.orders.insert_order(_order)
            return _order
        else:
            print(
                'ERROR : CODE {} TIME {}  AMOUNT {} TOWARDS {}'.format(
                    code,
                    time,
                    amount,
                    towards
                )
            )
            print(wrong_reason)
            return False

    def make_deal(self, order: dict):

        self.receive_deal(order["instrument_id"], trade_price=order["limit_price"], trade_time=self.datetime,
                          trade_amount=order["volume_left"], trade_towards=order["towards"],
                          order_id=order['order_id'], trade_id=str(uuid.uuid4()))

    def receive_deal(self,
                     code,
                     trade_id: str,
                     order_id: str,
                     realorder_id: str,
                     trade_price,
                     trade_amount,
                     trade_towards,
                     trade_time,
                     message=None):
        # if order_id in self.orders.keys():

        #     # update order
        #     od = self.orders[order_id]
        #     frozen = self.frozen.get(
        #         order_id, {'order_id': order_id, 'money': 0, 'price': 0})
        #     vl = od.get('volume_left', 0)
        #     if trade_amount == vl:

        #         self.money += frozen['money']
        #         frozen['amount'] = 0
        #         frozen['money'] = 0
        #         od['last_msg'] = '????????????'
        #         od["status"] = 300
        #         self.log('???????????? {}'.format(order_id))

        #     elif trade_amount < vl:
        #         frozen['amount'] = vl - trade_amount
        #         release_money = trade_amount * frozen['coeff']
        #         self.money += release_money

        #         frozen['money'] -= release_money

        #         od['last_msg'] = '????????????'
        #         od["status"] = 200
        #         self.log('???????????? {}'.format(order_id))

        #     od['volume_left'] -= trade_amount

        #     self.orders[order_id] = od
        #     self.frozen[order_id] = frozen
        #     # update trade
        #     self.event_id += 1
        #     trade_id = str(uuid.uuid4()) if trade_id is None else trade_id

        return self.receive_simpledeal(
            code,
            trade_price,
            trade_amount,
            trade_towards,
            trade_time,
            message=message,
            order_id=order_id,
            trade_id=trade_id,
            realorder_id=realorder_id)

    def receive_simpledeal(self,
                           code,
                           trade_price,
                           trade_amount,
                           trade_towards,
                           trade_time,
                           message=None,
                           order_id=None,
                           trade_id=None,
                           realorder_id=None):

        pos = self.get_position(code)
        self.datetime = trade_time
        if realorder_id in self.finishedOrderid:
            pass
        else:
            self.finishedOrderid.append(realorder_id)
        market_towards = 1 if trade_towards > 0 else -1
        # value ???????????? unit ????????????
        if self.allow_margin:
            frozen = self.market_preset.get_frozen(
                code)                  # ????????????
            unit = self.market_preset.get_unit(
                code)                      # ????????????
            raw_trade_money = trade_price * trade_amount * market_towards  # ?????????
            value = raw_trade_money * unit                                # ???????????????
            trade_money = value * frozen                                  # ???????????????
        else:
            trade_money = trade_price * trade_amount * market_towards
            raw_trade_money = trade_money
            value = trade_money
            unit = 1
            frozen = 1
            # ????????????
            # trade_price

        if self.market_type == MARKET_TYPE.FUTURE_CN:
            # ???????????????
            # ??????????????? ??????????????????????????????

            commission_fee_preset = self.market_preset.get_code(code)
            if trade_towards in [ORDER_DIRECTION.BUY_OPEN,
                                 ORDER_DIRECTION.BUY_CLOSE,
                                 ORDER_DIRECTION.SELL_CLOSE,
                                 ORDER_DIRECTION.SELL_OPEN]:
                commission_fee = commission_fee_preset['commission_coeff_pervol'] * trade_amount + \
                    commission_fee_preset['commission_coeff_peramount'] * \
                    abs(value)
            elif trade_towards in [ORDER_DIRECTION.BUY_CLOSETODAY,
                                   ORDER_DIRECTION.SELL_CLOSETODAY]:
                commission_fee = commission_fee_preset['commission_coeff_today_pervol'] * trade_amount + \
                    commission_fee_preset['commission_coeff_today_peramount'] * \
                    abs(value)

            tax_fee = 0  # ?????????????????????
        elif self.market_type == MARKET_TYPE.STOCK_CN:

            commission_fee = self.commission_coeff * \
                abs(trade_money)

            commission_fee = 5 if commission_fee < 5 else commission_fee
            if int(trade_towards) > 0:
                tax_fee = 0  # ?????????????????????
            else:
                tax_fee = self.tax_coeff * abs(trade_money)

        # ????????????
        if self.cash[-1] > trade_money + commission_fee + tax_fee:
            self.time_index_max.append(trade_time)
            # TODO: ?????????????????????????????????
            if self.allow_sellopen:
                if trade_towards in [ORDER_DIRECTION.BUY_OPEN,
                                     ORDER_DIRECTION.SELL_OPEN]:
                    # ????????????????????? ??????avg
                    # ?????????
                    if code in self.frozen.keys():
                        if str(trade_towards) in self.frozen[code].keys():
                            pass
                        else:
                            self.frozen[code][str(trade_towards)] = {
                                'money': 0,
                                'amount': 0,
                                'avg_price': 0
                            }
                    else:
                        self.frozen[code] = {
                            str(ORDER_DIRECTION.BUY_OPEN): {
                                'money': 0,
                                'amount': 0,
                                'avg_price': 0
                            },
                            str(ORDER_DIRECTION.SELL_OPEN): {
                                'money': 0,
                                'amount': 0,
                                'avg_price': 0
                            }
                        }
                    """[summary]
                    # frozen?????????
                    # money ???????????????
                    # amount  ???????????????

                    2018-12-31

                    ????????????[money] ??????

                    ?????????
                    raw_trade_money =  trade_price * trade_amount * market_towards
                    ????????????(????????????*????????????*????????????)
                    trade_money =  trade_price * trade_amount * market_towards* unit * frozen

                    money = (money*amount + trade_money)/(amount+???????????????)
                    avg_price= (avgprice*amount+ raw_trade_money)/(amount+???????????????)

                    """

                    self.frozen[code][str(trade_towards)]['money'] = (
                        (
                            self.frozen[code][str(trade_towards)]['money'] *
                            self.frozen[code][str(trade_towards)]['amount']
                        ) + abs(trade_money)
                    ) / (
                        self.frozen[code][str(trade_towards)]['amount'] +
                        trade_amount
                    )
                    self.frozen[code][str(trade_towards)]['avg_price'] = (
                        (
                            self.frozen[code][str(trade_towards)]['avg_price'] *
                            self.frozen[code][str(trade_towards)]['amount']
                        ) + abs(trade_money)
                    ) / (
                        self.frozen[code][str(trade_towards)]['amount'] +
                        trade_amount
                    )
                    self.frozen[code][str(trade_towards)
                                      ]['amount'] += trade_amount

                    self.cash.append(
                        self.cash[-1] - abs(trade_money) - commission_fee -
                        tax_fee
                    )
                    #pos.update_pos(trade_price, trade_amount, trade_towards)
                elif trade_towards in [ORDER_DIRECTION.BUY_CLOSE,
                                       ORDER_DIRECTION.BUY_CLOSETODAY,
                                       ORDER_DIRECTION.SELL_CLOSE,
                                       ORDER_DIRECTION.SELL_CLOSETODAY]:
                    # ?????????????????????
                    # if trade_towards == ORDER_DIRECTION.BUY_CLOSE:
                    # ???????????? ????????????
                    # self.cash
                    # ????????????  ???????????????
                    if trade_towards in [ORDER_DIRECTION.BUY_CLOSE,
                                         ORDER_DIRECTION.BUY_CLOSETODAY]:
                        # self.frozen[code][ORDER_DIRECTION.SELL_OPEN]['money'] -= trade_money
                        self.frozen[code][str(ORDER_DIRECTION.SELL_OPEN
                                              )]['amount'] -= trade_amount

                        frozen_part = self.frozen[code][str(
                            ORDER_DIRECTION.SELL_OPEN
                        )]['money'] * trade_amount
                        # ???????????????+ ?????????????????? + ????????????* ?????? - ????????????
                        """
                        + ?????????????????? frozen_part ????????????* ?????????????????????????????????
                        + ??????????????????????????????????????????????????????????????? (frozen_part - trade_money)/frozen
                        - ?????????
                        - ??????

                        ???:

                        ?????? 3800
                        ????????????  3700

                        ???????????????: 3838

                        + ??????: 3700
                        + ??????: (-3700 + 3737)*??????/???????????? ==> ???????????? [??????????????????: ???????????? -3700 ???????????? + 3737]
                        - ?????????


                        ?????? 3800
                        ???????????? ?????? 3700
                        ???????????????: 3838

                        + ??????: 3700
                        + ??????: (-3737 + 3700)*??????/???????????? ==> ???????????? [???????????????????????????: ????????????=> 3700 ???????????? -3737]
                        - ?????????

                        """
                        self.cash.append(
                            self.cash[-1] + frozen_part +
                            (frozen_part - trade_money) / frozen -
                            commission_fee - tax_fee
                        )
                        if self.frozen[code][str(
                                ORDER_DIRECTION.SELL_OPEN)]['amount'] == 0:
                            self.frozen[code][str(ORDER_DIRECTION.SELL_OPEN
                                                  )]['money'] = 0
                            self.frozen[code][str(ORDER_DIRECTION.SELL_OPEN
                                                  )]['avg_price'] = 0

                    # ????????????  ???????????????
                    elif trade_towards in [ORDER_DIRECTION.SELL_CLOSE,
                                           ORDER_DIRECTION.SELL_CLOSETODAY]:
                        # self.frozen[code][ORDER_DIRECTION.BUY_OPEN]['money'] -= trade_money
                        self.frozen[code][str(ORDER_DIRECTION.BUY_OPEN
                                              )]['amount'] -= trade_amount

                        frozen_part = self.frozen[code][str(
                            ORDER_DIRECTION.BUY_OPEN
                        )]['money'] * trade_amount
                        self.cash.append(
                            self.cash[-1] + frozen_part +
                            (abs(trade_money) - frozen_part) / frozen -
                            commission_fee - tax_fee
                        )
                        if self.frozen[code][str(
                                ORDER_DIRECTION.BUY_OPEN)]['amount'] == 0:
                            self.frozen[code][str(ORDER_DIRECTION.BUY_OPEN
                                                  )]['money'] = 0
                            self.frozen[code][str(ORDER_DIRECTION.BUY_OPEN
                                                  )]['avg_price'] = 0
            else:  # ????????????????????????==> ??????

                self.cash.append(
                    self.cash[-1] - trade_money - tax_fee - commission_fee
                )
            if self.allow_t0 or trade_towards == ORDER_DIRECTION.SELL:
                self.sell_available[code] = self.sell_available.get(
                    code,
                    0
                ) + trade_amount * market_towards
                self.buy_available = self.sell_available

            self.cash_available = self.cash[-1]
            frozen_money = abs(trade_money) if trade_towards in [
                ORDER_DIRECTION.BUY_OPEN,
                ORDER_DIRECTION.SELL_OPEN
            ] else 0

            try:
                total_frozen = sum([itex.get('avg_price', 0) * itex.get('amount', 0)
                                    for item in self.frozen.values() for itex in item.values()])
            except Exception as e:
                print(e)
                total_frozen = 0
            self.history.append(
                [
                    str(trade_time),
                    code,
                    trade_price,
                    market_towards * trade_amount,
                    self.cash[-1],
                    order_id,
                    realorder_id,
                    trade_id,
                    self.account_cookie,
                    commission_fee,
                    tax_fee,
                    message,
                    frozen_money,
                    trade_towards,
                    total_frozen
                ]
            )
            pos.update_pos(trade_price, trade_amount, trade_towards)
            return 0

        else:
            print('ALERT MONEY NOT ENOUGH!!!')
            print(self.cash[-1])
            self.cash_available = self.cash[-1]
            return -1
            #print('NOT ENOUGH MONEY FOR {}'.format(order_id))

    def settle(self, settle_data=None):
        """
        ??????/??????????????????

        ???????????????:  ????????????????????????
        T0?????????: ??????T0?????????

        ???????????????: ??????????????????


        @2019-02-25 yutiansut
        hold ???????????????????????????:

        ??? ??????????????? ==> ??????+??????+????????? (??????????????????)

        ??????history?????????hold ==> last_settle+ today_pos_change

        """
        #print('FROM QUANTAXIS QA_ACCOUNT: account settle')
        if self.running_environment == RUNNING_ENVIRONMENT.TZERO and self.hold_available.sum(
        ) != 0:
            raise RuntimeError(
                'QAACCOUNT: ???T0?????????????????????,????????? {}'.format(
                    self.hold_available.to_dict()
                )
            )
        if self.market_type == MARKET_TYPE.FUTURE_CN:
            # ????????????????????????

            self.static_balance['frozen'].append(
                sum(
                    [
                        rx['money'] * rx['amount']
                        for var in self.frozen.values()
                        for rx in var.values()
                    ]
                )
            )

            self.static_balance['cash'].append(self.cash[-1])
            self.static_balance['hold'].append(self.hold.to_dict())
            self.static_balance['date'].append(self.date)
            """?????????????????????

            ??????????????????/ ??????????????????

            ?????????????????????????????????

            """

            self.static_balance['static_assets'].append(
                self.static_balance['cash'][-1] +
                self.static_balance['frozen'][-1]
            )

        self.sell_available = self.hold
        self.buy_available = self.hold
        self.cash_available = self.cash[-1]
        self.datetime = '{} 09:30:00'.format(
            QA_util_get_next_day(self.date)
        ) if self.date is not None else None
        for item in self.positions.values():
            item.settle()

    def from_message(self, message):
        """resume the account from standard message
        ?????????????????????????????????????????????"""
        self.account_cookie = message.get('account_cookie', None)
        self.portfolio_cookie = message.get('portfolio_cookie', None)
        self.user_cookie = message.get('user_cookie', None)
        self.broker = message.get('broker', None)
        self.market_type = message.get('market_type', None)
        self.strategy_name = message.get('strategy_name', None)
        self._currenttime = message.get('current_time', None)
        self.allow_sellopen = message.get('allow_sellopen', False)
        self.allow_margin = message.get('allow_margin', False)
        self.allow_t0 = message.get('allow_t0', False)
        self.margin_level = message.get('margin_level', False)
        self.frequence = message.get(
            'frequence',
            FREQUENCE.FIFTEEN_MIN
        )                                       # ??????15min
        self.init_cash = message.get(
            'init_cash',
            message.get('init_assets',
                        1000000)
        )                                       # ????????????
        self.init_hold = pd.Series(message.get('init_hold', {}), name='amount')
        self.init_hold.index.name = 'code'
        self.commission_coeff = message.get('commission_coeff', 0.00015)
        self.tax_coeff = message.get('tax_coeff', 0.0015)
        self.history = message['history']
        self.cash = message['cash']
        self.time_index_max = message['trade_index']
        self.running_time = message.get('running_time', None)
        self.quantaxis_version = message.get('quantaxis_version', None)
        self.running_environment = message.get(
            'running_environment',
            RUNNING_ENVIRONMENT.BACKETEST
        )
        self.frozen = message.get('frozen', {})
        self.finishedOrderid = message.get('finished_id', [])
        self.settle()
        return self

    def from_otgdict(self, message):
        """[summary]
        balance = static_balance + float_profit


            "currency": "",  # "CNY" (??????)
            "pre_balance": float("nan"),  # 9912934.78 (??????????????????)
            "static_balance": float("nan"),  # (????????????)
            "balance": float("nan"),  # 9963216.55 (????????????)
            "available": float("nan"),  # 9480176.15 (????????????)
            "float_profit": float("nan"),  # 8910.0 (????????????)
            "position_profit": float("nan"),  # 1120.0(????????????)
            "close_profit": float("nan"),  # -11120.0 (???????????????????????????)
            "frozen_margin": float("nan"),  # 0.0(???????????????)
            "margin": float("nan"),  # 11232.23 (???????????????)
            "frozen_commission": float("nan"),  # 0.0 (???????????????)
            "commission": float("nan"),  # 123.0 (?????????????????????????????????)
            "frozen_premium": float("nan"),  # 0.0 (???????????????)
            "premium": float("nan"),  # 0.0 (?????????????????????????????????)
            "deposit": float("nan"),  # 1234.0 (??????????????????????????????)
            "withdraw": float("nan"),  # 890.0 (??????????????????????????????)
            "risk_ratio": float("nan"),  # 0.048482375 (?????????)
        """

        self.allow_margin = True
        self.allow_sellopen = True
        self.allow_t0 = True

        self.account_cookie = message['accounts']['user_id']
        # ????????????
        self.cash_available = message['accounts']['available']
        self.balance = message['accounts']['balance']

        # ?????????????????????????????????
        # ????????????/???????????? ==> ?????????????????????
        self.static_balance = message['accounts']['static_balance']
        self.pre_balance = message['accounts']['pre_balance']

        # ????????????
        self.close_profit = message['accounts']['close_profit']
        # ????????????
        self.position_profit = message['accounts']['position_profit']

        # ????????????
        self.float_profit = message['accounts']['float_profit']

        # ???????????????
        self.margin = message['accounts']['margin']

        self.commission = message['accounts']['commission']

    def save(self):
        """
        ??????????????????
        """
        save_account(self.message)

    def reload(self):
        print('QAACCPRO: reload from DATABASE')
        message = self.client.find_one(
            {
                'account_cookie': self.account_cookie,
                'portfolio_cookie': self.portfolio_cookie,
                'user_cookie': self.user_cookie
            }
        )

        if message is None:
            self.client.insert(self.message)
        else:
            self.from_message(message)

    def sync_account(self, sync_message):
        """????????????

        Arguments:
            sync_message {[type]} -- [description]
        """

        self.init_hold = sync_message['hold_available']
        self.init_cash = sync_message['cash_available']

        self.sell_available = copy.deepcopy(self.init_hold)
        self.history = []
        self.cash = [self.init_cash]
        self.cash_available = self.cash[-1]  # ????????????

    def change_cash(self, money):
        """
        ????????????|??????|
        """
        res = self.cash[-1] + money
        if res >= 0:
            # ????????????
            self.cash[-1] = res

    def get_orders(self, if_today=True):
        '''
        ??????????????????/????????????
        :param if_today: true ????????????????????????
        :return: QA_OrderQueue
        '''
        # ????todo ???????????????????????????????????????
        return self.orders

    def get_history(self, start, end):
        """??????????????????

        Arguments:
            start {str} -- [description]
            end {str]} -- [description]
        """
        return self.history_table.set_index(
            'datetime',
            drop=False
        ).loc[slice(pd.Timestamp(start),
                    pd.Timestamp(end))]
