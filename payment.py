##############################################################################
#
#    GNU Condo: The Free Management Condominium System
#    Copyright (C) 2016- M. Alonso <port02.server@gmail.com>
#
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import datetime
import os
import unicodedata
from itertools import groupby, chain

import unicodecsv
from cStringIO import StringIO
from decimal import Decimal, DecimalException

import genshi
import genshi.template
from sql import Literal
from sql.aggregate import Count, Max

from trytond.pool import Pool
from trytond.model import ModelSQL, ModelView, Workflow, fields, dualmethod, Unique
from trytond.pyson import Eval, If, Not, Bool
from trytond.transaction import Transaction
from trytond.tools import reduce_ids, grouped_slice

from trytond.modules.company import CompanyReport


__all__ = ['CondoPain', 'CondoPaymentGroup',
           'CondoPayment', 'CondoMandate', 'CondoMandateReport']


class CondoPain(Workflow, ModelSQL, ModelView):
    'Condominium Payment Initation Message'
    __name__ = 'condo.payment.pain'
    reference = fields.Char('Reference', required=True,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    company = fields.Many2One('company.company', 'Initiating Party',
        domain=[
            ('party.active', '=', True),
            ],
        select=True, required=True,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    sepa_receivable_flavor = fields.Selection([
#            (None, ''),
            ('pain.008.001.02', 'pain.008.001.02'),
#            ('pain.008.001.04', 'pain.008.001.04'),
            ], 'Receivable Flavor', required=True,
        states={
            'readonly': Eval('state') != 'draft',
            'required': Eval('state') != 'draft',
            },
        depends=['state'],
        translate=False)
    groups = fields.One2Many('condo.payment.group', 'pain', 'Payments Groups',
        add_remove=[ 'OR',
                      [ ('pain', '=', None),
                        If(Bool(Eval('company')), [ 'OR',
                                                   ('company', '=', Eval('company')),
                                                   ('company.parent', 'child_of', Eval('company'))
                                                  ],
                                                  []
                          )
                      ],
                      ('pain', '=', Eval('id', -1))
                   ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['company', 'state'])
    message = fields.Text('Message',
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    state = fields.Selection([
            ('draft', 'Draft'),
            ('generated', 'Generated'),
            ('booked', 'Booked'),
            ('rejected', 'Rejected'),
            ], 'State', readonly=True, select=True)
    nboftxs = fields.Function(fields.Integer('Number of Transactions'),
        'get_nboftxs')
    ctrlsum = fields.Function(fields.Numeric('Control Sum', digits=(11,2)),
        'get_ctrlsum')

    @classmethod
    def __setup__(cls):
        super(CondoPain, cls).__setup__()
        cls._error_messages.update({
                'generate_error': ('Can not generate message "%s" of "%s"'),
                })
        cls._transitions |= set((
                ('draft', 'generated'),
                ('generated', 'draft'),
                ('generated', 'booked'),
                ('generated', 'rejected'),
                ('booked', 'rejected'),
                ('rejected', 'draft'),
                ))
        cls._buttons.update({
                'cancel': {
                    'invisible': ~Eval('state').in_(
                        ['generated', 'booked']),
                    },
                'draft': {
                    'invisible': ~Eval('state').in_(
                        ['generated', 'rejected']),
                    },
                'generate': {
                    'invisible': Eval('state') != 'draft',
                    },
                'accept': {
                    'invisible': Eval('state') != 'generated',
                    },
                })
        t = cls.__table__()
        cls._sql_constraints += [
            ('reference_unique', Unique(t,t.company, t.reference),
                'The reference must be unique for each party!'),
        ]

    @classmethod
    def validate(cls, pains):
        super(CondoPain, cls).validate(pains)
        for pain in pains:
            pain.company_has_sepa_creditor_identifier()

    def company_has_sepa_creditor_identifier(self):
        if not self.company.sepa_creditor_identifier:
            self.raise_user_error(
                "Initiating Party must have a sepa creditor identifier")

    @staticmethod
    def default_sepa_receivable_flavor():
        return 'pain.008.001.02'

    @staticmethod
    def default_state():
        return 'draft'

    def get_nboftxs(self, name):
        if self.groups:
            #http://stackoverflow.com/questions/952914/making-a-flat-list-out-of-list-of-lists-in-python?rq=1
            #leaf for tree in forest for leaf in tree
            return sum(1 for group in self.groups for p in group.payments if p.amount)

    def get_ctrlsum(self, name):
        if self.groups:
           return sum(p.amount for group in self.groups for p in group.payments if p.amount)

    def sepa_group_payment_key(self, payment):
        key = (('date', payment.date),)
        key += (('group', payment.group),)
        key += (('sequence_type', payment.sequence_type),)
        key += (('scheme', payment.sepa_mandate.scheme),)
        return key

    @property
    def sepa_payments(self):
        keyfunc = self.sepa_group_payment_key
        payments = sorted([payment for group in self.groups for payment in group.payments], key=keyfunc)
        for key, grouped_payments in groupby(payments, key=keyfunc):
            yield dict(key), list(grouped_payments)

    def get_sepa_template(self):
        if self.sepa_receivable_flavor:
            return loader.load('%s.xml' % self.sepa_receivable_flavor)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, pains):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().cursor

        pids = [p.id for payments in [group.payments for pain in pains for group in pain.groups] for p in payments]
        for sub_ids in grouped_slice(pids):
            red_sql = reduce_ids(payment.id, sub_ids)
            cursor.execute(*payment.update(
                    columns=[payment.state],
                    values=['draft'],
                    where=red_sql))

    @dualmethod
    @ModelView.button
    @Workflow.transition('generated')
    def generate(cls, pains):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().cursor

        for pain in pains:
            pids = [p.id for group in pain.groups for p in group.payments]
            for sub_ids in grouped_slice(pids):
                red_sql = reduce_ids(payment.id, sub_ids)

                cursor.execute(*payment.update(
                        columns=[payment.state],
                        values=['approved'],
                        where=red_sql))
            try:
                tmpl = pain.get_sepa_template()
                message = tmpl.generate(pain=pain,
                    datetime=datetime, normalize=unicodedata.normalize,
                    ).filter(remove_comment).render()
                pain.message = message

                pain.save()
            except:
                cursor.rollback()
                cls.raise_user_error('generate_error', (pain.reference,pain.company.party.name))
            else:
                cursor.commit()

    @classmethod
    @ModelView.button
    @Workflow.transition('booked')
    def accept(cls, pains):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().cursor

        pids = [p.id for payments in [group.payments for pain in pains for group in pain.groups] for p in payments]
        for sub_ids in grouped_slice(pids):
            red_sql = reduce_ids(payment.id, sub_ids)
            cursor.execute(*payment.update(
                    columns=[payment.state],
                    values=['processing'],
                    where=red_sql))

    @classmethod
    @ModelView.button
    @Workflow.transition('rejected')
    def cancel(cls, pains):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().cursor

        pids = [p.id for payments in [group.payments for pain in pains for group in pain.groups] for p in payments]
        for sub_ids in grouped_slice(pids):
            red_sql = reduce_ids(payment.id, sub_ids)
            cursor.execute(*payment.update(
                    columns=[payment.state],
                    values=['failed'],
                    where=red_sql))

def remove_comment(stream):
    for kind, data, pos in stream:
        if kind is genshi.core.COMMENT:
            continue
        yield kind, data, pos


loader = genshi.template.TemplateLoader(
    os.path.join(os.path.dirname(__file__), 'template'),
    auto_reload=True)


class CondoPaymentGroup(ModelSQL, ModelView):
    'Condominium Payment Group'
    __name__ = 'condo.payment.group'
    _rec_name = 'reference'
    reference = fields.Char('Reference', required=True,
        states={
            'readonly': Bool(Eval('readonly'))
            },
        depends=['readonly'])
    company = fields.Many2One('company.company', 'Condominium',
        domain=[
            ('party.active', '=', True),
            ('is_Condominium', '=', True)
            ],
        select=True, required=True,
        states={
            'readonly': Eval('id', 0) > 0
            })
    pain = fields.Many2One('condo.payment.pain', 'Pain Message',
        ondelete='SET NULL')
    account_number = fields.Many2One('bank.account.number', 'Account Number',
        ondelete='RESTRICT',
        domain=[
            ('type', '=', 'iban'),
            If(Bool(Eval('readonly')),
                                     [],
                                     [('account.active', '=', True),]
            ),
            ('account.owners.companies', '=', Eval('company')),
            ],
        states={
            'readonly': Bool(Eval('readonly'))
            },
        depends=['company', 'readonly'], required=True)
    date = fields.Date('Debit Date', required=True,
        states={
            'readonly': Bool(Eval('readonly'))
            },
        depends=['readonly'])
    payments = fields.One2Many('condo.payment', 'group', 'Payments')
    sepa_batch_booking = fields.Boolean('Batch Booking',
        states={
            'readonly': Bool(Eval('readonly'))
            },
        depends=['readonly'])
    sepa_charge_bearer = fields.Selection([
            ('DEBT', 'Debtor'),
            ('CRED', 'Creditor'),
            ('SHAR', 'Shared'),
            ('SLEV', 'Service Level'),
            ], 'Charge Bearer', required=True,
        states={
            'readonly': Bool(Eval('readonly'))
            },
        depends=['readonly'])
    message = fields.Text('Message',
        states={
            'readonly': Bool(Eval('readonly'))
            },
        depends=['readonly'])
    nboftxs = fields.Function(fields.Integer('Number of Transactions'),
        'get_nboftxs')
    ctrlsum = fields.Function(fields.Numeric('Control Sum', digits=(11,2)),
        'get_ctrlsum')
    readonly = fields.Function(fields.Boolean('State'),
        getter='get_readonly', searcher='search_readonly')

    @classmethod
    def __setup__(cls):
        super(CondoPaymentGroup, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('reference_unique', Unique(t,t.company, t.reference),
                'The reference must be unique for each condominium!'),
        ]
        cls._error_messages.update({
                'readonly_paymentgroup': ('PaymentGroup "%s" is in readonly state'
                    ),
                'payments_approved': ('PaymentGroup "%s" has payments approved'
                    ' with earlier date.'),
                })
        cls._buttons.update({
                'generate_fees': {
                    'invisible': Bool(Eval('readonly'))},
                })

    @classmethod
    def validate(cls, paymentgroups):
        super(CondoPaymentGroup, cls).validate(paymentgroups)

        table = cls.__table__()
        payments = Pool().get('condo.payment').__table__()

#TODO (remove hack)
        user = Transaction().user
        if user==1:
            return

        for paymentgroup in paymentgroups:
            if paymentgroup.readonly:
                with Transaction().new_cursor(readonly=True):
                    cursor = Transaction().cursor
                    cursor.execute(*table.select(table.date,
                                 where=(table.id == paymentgroup.id) &
                                       (table.date != paymentgroup.date)))
                    if cursor.fetchone():
                        cls.raise_user_error('readonly_paymentgroup', (paymentgroup.reference)
                            )
                return

            cursor = Transaction().cursor
            cursor.execute(*payments.select(payments.id,
                         where=(payments.group == paymentgroup.id) &
                               (payments.date < paymentgroup.date) &
                               (payments.state != 'draft')))
            if cursor.fetchall():
                cls.raise_user_error('payments_approved', (paymentgroup.reference)
                    )

            paymentgroup.check_today()
            paymentgroup.check_businessdate()
            paymentgroup.company_has_sepa_creditor_identifier()

            #if has drafted payments with due date before new date
            #update date field of payments
            cursor.execute(*payments.select(payments.id,
                         where=(payments.group == paymentgroup.id) &
                               (payments.date < paymentgroup.date) &
                               (payments.state == 'draft')))
            ids_draft = [ids for (ids,) in cursor.fetchall()]

            if len(ids_draft):
                for sub_ids in grouped_slice(ids_draft):
                    red_sql = reduce_ids(payments.id, sub_ids)
                    # Use SQL to prevent double validate loop
                    cursor.execute(*payments.update(
                            columns=[payments.date],
                            values=[paymentgroup.date],
                            where=red_sql))

    def check_today(self):
        if self.date:
            pool = Pool()
            Date = pool.get('ir.date')
            d = Date.today()
            if self.date<d:
                self.raise_user_error(
                    'Must select date after today!')

    def check_businessdate(self):
        if self.date and self.date.weekday() in (5,6):
            self.raise_user_error(
                "Date must be a business day!")

    def company_has_sepa_creditor_identifier(self):
        if not self.company.sepa_creditor_identifier:
            self.raise_user_error(
                "Company must have a sepa creditor identifier")

    @staticmethod
    def default_date():
        pool = Pool()
        Date = pool.get('ir.date')
        d = Date.today()
        #set tomorrow (or the next business day after tomorrow) as date
        next = d + datetime.timedelta(days= 7-d.weekday() if d.weekday()>3 else 1)
        return next

    @staticmethod
    def default_sepa_batch_booking():
        return True

    @staticmethod
    def default_sepa_charge_bearer():
        return 'SLEV'

    def get_nboftxs(self, name):
        if self.payments:
            #return len(self.payments)
            #we get only payments with valid amount
            return sum(1 for p in self.payments if p.amount)

    def get_ctrlsum(self, name):
        if self.payments:
            return sum(p.amount for p in self.payments if p.amount)

    def get_readonly(self, name):
        return self.pain.state!='draft' if self.pain else False

    @classmethod
    def search_readonly(cls, name, domain):
        _, operator, value = domain
        pool = Pool()
        table1 = pool.get('condo.payment.pain').__table__()
        table2 = cls.__table__()

#TODO (remove hack)
        user = Transaction().user
        if user<=1:
            return []

        if (operator=='=' and not value) or (operator=='!=' and value):
            query1 = table1.join(table2,
                            condition=table1.id == table2.pain).select(
                                 table2.id,
                                 where=table1.state == 'draft')
            query2 = table2.select(table2.id,
                                 where=table2.pain == None)
            return [ 'OR', ('id', 'in', query1), ('id', 'in', query2)]
        else:
            query = table1.join(table2,
                            condition=table1.id == table2.pain).select(
                                 table2.id,
                                 where=table1.state != 'draft')
            return [('id', 'in', query)]

    @staticmethod
    def order_company(tables):
        table, _ = tables[None]
        return [table.company, table.date]

    @staticmethod
    def order_reference(tables):
        table, _ = tables[None]
        return [table.reference, table.company]

    @dualmethod
    @ModelView.button
    def generate_fees(cls, groups, _save=True):
        pool = Pool()

        CondoParties = pool.get('condo.party')
        CondoPayments = pool.get('condo.payment')

        for group in groups:
            condoparties = CondoParties.search([('unit.company', '=', group.company),
                ('sepa_mandate', '!=', None),
                ('sepa_mandate.state', 'not in', ['draft', 'canceled']),
                ('sepa_mandate.account_number', '!=', None),
                ], order=[('unit.name', 'ASC'),])

            if group.message:
                message = group.message.encode('utf-8')
                f = StringIO(message)
                r = unicodecsv.reader(f, delimiter=';', encoding='utf-8')
                information = list(map(tuple, r))

            #delete payments of this group with state='draft'
            CondoPayments.delete([p for p in group.payments if p.state=='draft'])

            for condoparty in condoparties:
                if CondoPayments.search_count([
                               ('group', '=', group),
                               ('unit', '=', condoparty.unit),
                               ('party', '=', condoparty.party)])==0:
                    condopayment = CondoPayments(
                                      group = group,
                                      fee = True,
                                      unit = condoparty.unit,
                                      #Set the condoparty as the party
                                      #(instead the debtor of the mandate condoparty.sepa_mandate.party)
                                      party = condoparty.party,
                                      currency = group.company.currency,
                                      sepa_mandate = condoparty.sepa_mandate,
                                      type = condoparty.sepa_mandate.type,
                                      date = group.date,
                                      sepa_end_to_end_id = condoparty.unit.name)
                    #Read rest fields from message file
                    if group.message and len(information):
                        concepts = [x for x in information if x[0]==condoparty.unit.name]
                        for concept in concepts:
                            if ((len(concept)==4 and (condoparty.role==concept[3] if bool(concept[3]) else not bool(condoparty.role)))
                                or (len(concept)==3 and len(concepts)==1)):
                                    try:
                                        condopayment.amount = Decimal(concept[1].replace(",", "."))
                                        condopayment.description = concept[2]
                                    except DecimalException:
                                        cls.raise_user_error('Amount of fee for unit "%s" is invalid!',
                                                              condoparty.unit.name)

                                    if condopayment.amount<=0:
                                        cls.raise_user_warning('warn_invalid_amount.%d.%d' % (group.id, condoparty.id),
                                            'Amount of fee for unit "%s" must be bigger than zero!', condoparty.unit.name)

                                    #Consider only condopayments included in group.message
                                    group.payments += (condopayment,)
        if _save:
            cls.save(groups)

    @classmethod
    def PreparePaymentGroup(cls):
        id = Transaction().context.get('company') or None

        if id:
            Company = Pool().get('company.company')
            condo = Company.search([('id', '=', id),
                                    ('is_Condominium', '=', True),
                                    ('sepa_creditor_identifier', '!=', None),
                                    ('party.active', '=', True),
                                  ])

            if (len(condo)==1 and len(condo[0].sepa_mandates)>0):
                BankAccountNumber = Pool().get('bank.account.number')
                bankaccountnumber = BankAccountNumber.search([('account.id', 'in', condo[0].party.bank_accounts),
                                                              ('account.active', '=', True),
                                                              ('type', '=', 'iban'),
                                                             ])
                if len(bankaccountnumber)==1: #Only condos with one bank account number
                    Date = Pool().get('ir.date')
                    d = Date.today()
                    ddd = d.replace(day = 2,
                                    month = d.month+1 if d.month<12 else 1,
                                    year = d.year if d.month<12 else d.year+1)
                    paymentgroup = cls(reference = '{:04d}'.format(ddd.year) +
                                                   '_' +
                                                   '{:02d}'.format(ddd.month) +
                                                   '-' +
                                                   bankaccountnumber[0].number_compact [8:12] +
                                                   '.' +
                                                   '{:04d}'.format(ddd.year)[-2:],
                                       company = condo[0],
                                       account_number = bankaccountnumber[0],
                                       date = ddd + datetime.timedelta(days= 7-ddd.weekday() if ddd.weekday()>4 else 0),
                                       sepa_charge_bearer = 'SLEV')
                    paymentgroup.save()

    @classmethod
    def PrepareAllPaymentsGroup(cls):
        pool = Pool()
        banknumbers = pool.get('bank.account.number').__table__()
        bankaccounts = pool.get('bank.account').__table__()
        accountparties = pool.get('bank.account-party.party').__table__()
        parties = pool.get('party.party').__table__()
        companies = pool.get('company.company').__table__()

        cursor = Transaction().cursor

        #SELECT a.id, a.number, e.id, d.id, d.name FROM bank_account_number AS a
        #    INNER JOIN bank_account AS b ON a.account=b.id
        #    INNER JOIN "bank_account-party_party" AS c ON b.id=c.account
        #    INNER JOIN party_party AS d ON c.owner=d.id
        #    INNER JOIN company_company AS e ON d.id=e.party
        # WHERE a.type='iban' AND b.active<>0 AND d.active<>0 AND e.is_Condominium<>0 AND e.sepa_creditor_identifier<>""
        # GROUP BY e.id
        # HAVING COUNT(e.id)=1
        # ORDER BY e.id;
        cursor.execute(*banknumbers.join(bankaccounts,
                                     condition=banknumbers.account == bankaccounts.id).join(
                                     accountparties,
                                     condition=bankaccounts.id == accountparties.account).join(
                                     parties,
                                     condition=accountparties.owner == parties.id).join(
                                     companies,
                                     condition=parties.id == companies.party).select(
                                     Max(banknumbers.id), Max(banknumbers.number_compact), companies.id,
                                     where=((banknumbers.type == 'iban') &
                                            (bankaccounts.active == True) &
                                            (parties.active == True) &
                                            (companies.is_Condominium == True) &
                                            (companies.sepa_creditor_identifier != None)),
                                     group_by=companies.id,
                                     having=(Count(companies.id)==1)))

        Date = pool.get('ir.date')
        d = Date.today()
        ddd = d.replace(day = 2,
                        month = d.month+1 if d.month<12 else 1,
                        year = d.year if d.month<12 else d.year+1)

        #TODO: Check that condo has mandates
        values = []
        for (idb, number_compact, idc) in cursor.fetchall():
            record = {
                    'reference':          '{:04d}'.format(ddd.year) +
                                          '_' +
                                          '{:02d}'.format(ddd.month) +
                                          '-' +
                                          number_compact [8:12] +
                                          '.' +
                                          '{:04d}'.format(ddd.year)[-2:],
                    'company':            idc,
                    'account_number':     idb,
                    'date':               ddd +
                                          datetime.timedelta(days= 7-ddd.weekday() if ddd.weekday()>4 else 0),
                    'sepa_charge_bearer': 'SLEV'
                   }
            values.append(record)

        cls.create(values)


class CondoPayment(Workflow, ModelSQL, ModelView):
    'Condominium Payment'
    __name__ = 'condo.payment'
    group = fields.Many2One('condo.payment.group', 'Group',
        ondelete='RESTRICT', required=True,
        domain=[
                 ('readonly', '=', False),
               ],
        states={
            'readonly': Eval('id', 0) > 0
            })
    company = fields.Function(fields.Many2One('company.company', 'Company'),
        getter='get_company', searcher='search_company')
    fee = fields.Boolean('Fee', help="Check if this payment correspond to unit's fee",
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    unit = fields.Many2One('condo.unit', 'Unit',
        domain=[ If(Bool(Eval('group')),
                       [
                           ('company.groups_payments', '=', Eval('group'))
                       ],
                       []
                   )
               ],
        states={
            'readonly': Eval('state') != 'draft',
            'required': Bool(Eval('fee')),
            'invisible': Not(Bool(Eval('fee')))
            },
        depends=['group', 'fee'])
    unit_name=fields.Function(fields.Char('Unit'),
        getter='get_unit_name', searcher='search_unit_name')
    party = fields.Many2One('party.party', 'Ultimate Debtor', required=True,
        domain=[ If(Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                 [],[
                 If(Bool(Eval('fee')),
                       [
#This party is owner or tenant of the unit and have a mandate for it (on his own name or not)
                           ('units.sepa_mandate.company', If(Bool(Eval('company')), '=', '!='), Eval('company')),
                           ('units.sepa_mandate.state', 'not in', ['draft', 'canceled']),
                           ('units.active', '=', True),
                           ('units.unit', If(Bool(Eval('unit')), '=', '!='), Eval('unit'))
                       ],
                       [
#Subcondominium of the condominium with a mandate on his own name
                           ('sepa_mandates.state', 'not in', ['draft', 'canceled']),
                           ('sepa_mandates.company', '=', Eval('company')),
                       ]
                   )
                 ])
               ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['group', 'company', 'fee'])
    description = fields.Char('Description', size=140,
        states={
            'readonly': Eval('state') != 'draft',
            })
    currency = fields.Many2One('currency.currency', 'Currency',
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['group'])
    currency_digits = fields.Function(fields.Integer('Currency Digits'),
        'on_change_with_currency_digits')
    amount = fields.Numeric('Amount',
        digits=(11, Eval('currency_digits', 2)), states={
            'readonly': Eval('state') != 'draft',
            'required': Eval('state') != 'draft',
            },
        depends=['state', 'currency_digits'])
    sepa_mandate = fields.Many2One('condo.payment.sepa.mandate', 'Mandate',
        ondelete='RESTRICT', required=True,
        domain=[ If(Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                 [],[
                 ('state', 'not in', ['draft', 'canceled']),
                 If(Bool(Eval('company')),
                     [
                         ('company', '=', Eval('company')),
                     ],
                     []
                 ),
                 If(Bool(Eval('party')),
                       [ If(Bool(Eval('fee')),
                               [
                                   ('condoparties.party', '=', Eval('party'))
                               ],
                               [
                                   ('party', '=', Eval('party'))
                               ])
                       ],
                       []
                   )
                 ])
            ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['company', 'fee', 'party'])
    debtor = fields.Function(fields.Char('Debtor'),
        getter='get_debtor', searcher='search_debtor')
    type = fields.Selection([
            ('recurrent', 'RCUR'),
            ('one-off', 'OOFF'),
            ('final', 'FNAL'),
            ('first', 'FRST'),
            ], 'Sequence Type', required=True,
        states={
            'readonly': Eval('state').in_(
                        ['processing', 'succeeded', 'failed'])
            },
        depends=['sepa_mandate', 'state'])
    sepa_end_to_end_id = fields.Char('SEPA End To End ID', size=35,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['unit', 'sepa_mandate', 'state'])
    date = fields.Date('Debit Date', required=True,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['group', 'state'])
    state = fields.Selection([
            ('draft', 'Draft'),
            ('approved', 'Approved'),
            ('processing', 'Processing'),
            ('succeeded', 'Succeeded'),
            ('failed', 'Failed'),
            ], 'State', readonly=True, select=True)

    @classmethod
    def __setup__(cls):
        super(CondoPayment, cls).__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('unit.name', 'ASC'))
        cls._error_messages.update({
                'delete_draft': ('Payment "%s" must be in draft before '
                    'deletion.'),
                'readonly_payment': ('Payment of "%s"'
                    ' is not in draft state.'),
                'invalid_mandate': ('Payment of "%s" has an invalid'
                    ' mandate "%s"'),
                'invalid_draft': ('Message "%s" must be in draft to put'
                    ' payment of "%s" to "%s" in draft too.'),
                'invalid_succeeded': ('Message "%s" must be booked to put'
                    ' payment of "%s" to "%s" in succeeded state.'),
                })
        cls._transitions |= set((
                ('draft', 'approved'),
                ('approved', 'processing'),
                ('processing', 'succeeded'),
                ('processing', 'failed'),
                ('approved', 'draft'),
                ('succeeded', 'failed'),
                ('failed', 'succeeded'),
                ))
        cls._buttons.update({
                'draft': {
                    'invisible': Eval('state') != 'approved',
                    'icon': 'tryton-go-previous',
                    },
                'approve': {
                    'invisible': Eval('state') != 'draft',
                    'icon': 'tryton-go-next',
                    },
                'succeed': {
                    'invisible': ~Eval('state').in_(
                        ['processing', 'failed']),
                    'icon': 'tryton-ok',
                    },
                'fail': {
                    'invisible': ~Eval('state').in_(
                        ['processing', 'succeeded']),
                    'icon': 'tryton-cancel',
                    },
                })

    @classmethod
    def validate(cls, payments):
        super(CondoPayment, cls).validate(payments)

        table = cls.__table__()

        for payment in payments:
            if payment.state!='draft':
                with Transaction().new_cursor(readonly=True):
                    cursor = Transaction().cursor
                    cursor.execute(*table.select(table.id,
                                 where=(table.id == payment.id) &
                                       (table.date != payment.date)))
                    if cursor.fetchone():
                        cls.raise_user_error('readonly_payment', (payment.party.name)
                            )
                return
            payment.check_duedate()
            payment.check_businessdate()
            payment.check_account_number()

    def check_duedate(self):
        if self.group and self.group.date:
            if self.date<self.group.date:
                self.raise_user_error(
                    "Fee due date must be equal or bigger than his group date")

    def check_businessdate(self):
        if self.date and self.date.weekday() in (5,6):
            self.raise_user_error(
                "Date must be a business day!")

    def check_account_number(self):
        if not self.sepa_mandate.account_number:
            self.raise_user_error('invalid_mandate',
                (self.party.name, self.sepa_mandate.identification))

    @staticmethod
    def default_fee():
        return True

    @staticmethod
    def default_state():
        return 'draft'

    @fields.depends('group')
    def on_change_group(self):
        if self.group:
            self.company = self.group.company
            self.currency = self.group.company.currency
            self.currency_digits = self.group.company.currency.digits or 2
            self.date = self.group.date

    @fields.depends('group')
    def on_change_with_currency_digits(self, name=None):
        if self.group:
            return self.group.company.currency.digits
        return 2

    @fields.depends('sepa_mandate')
    def on_change_sepa_mandate(self):
        if self.sepa_mandate:
            self.type = self.sepa_mandate.type
        else:
            self.type = None
            self.debtor = ''

    @fields.depends('unit', 'sepa_mandate')
    def on_change_with_sepa_end_to_end_id(self, name=None):
        if self.unit:
            return self.unit.name
        elif self.sepa_mandate:
            return self.sepa_mandate.identification

    @classmethod
    def get_unit_name(cls, condopayments, name):
        return dict([ (p.id, p.unit.name if p.unit else None) for p in condopayments ])

    @classmethod
    def search_unit_name(cls, name, domain):
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]

        pool = Pool()
        table1 = pool.get('condo.unit').__table__()
        table2 = cls.__table__()

        query = table1.join(table2,
                        condition=table1.id == table2.unit).select(
                             table2.id,
                             where=Operator(table1.name, value))

        return [('id', 'in', query)]

    @classmethod
    def order_unit_name(cls, tables):
        return chain.from_iterable([cls.unit.convert_order('unit', tables, cls),
                cls.company.convert_order('company', tables, cls)])

    @classmethod
    def get_company(cls, condopayments, name):
        return dict([ (p.id, p.group.company.id if p.group else None) for p in condopayments ])

    @classmethod
    def search_company(cls, name, domain):
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]

        pool = Pool()
        table1 = pool.get('party.party').__table__()
        table2 = pool.get('company.company').__table__()
        table3 = pool.get('condo.payment.group').__table__()
        table4 = cls.__table__()

        query = table1.join(table2,
                        condition=table1.id == table2.party).join(table3,
                        condition=table2.id == table3.company).join(table4,
                        condition=table3.id == table4.group).select(
                             table4.id,
                             where=Operator(table1.name, value))

        return [('id', 'in', query)]

    @staticmethod
    def order_company(tables):
        pool = Pool()
        PaymentGroup = pool.get('condo.payment.group')

        field1 = PaymentGroup._fields['company']
        table, _ = tables[None]
        pgroup = PaymentGroup.__table__()

        order_tables1 = tables.get('group')
        if order_tables1 is None:
            order_tables1 = {
                    None: (pgroup, pgroup.id == table.group),
                    }
            tables['group'] = order_tables1

        Unit = pool.get('condo.unit')
        field2 = Unit._fields['name']
        unit = Unit.__table__()

        order_tables2 = tables.get('unit')
        if order_tables2 is None:
            order_tables2 = {
                    None: (unit, unit.id == table.unit),
                    }
            tables['unit'] = order_tables2

        return chain.from_iterable([field1.convert_order('company', order_tables1, PaymentGroup),
                                    field2.convert_order('name', order_tables2, Unit)])

    @classmethod
    def get_debtor(cls, condopayments, name):
        return dict([ (p.id, p.sepa_mandate.party.name if p.sepa_mandate else None) for p in condopayments ])

    @classmethod
    def search_debtor(cls, name, domain):
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]

        pool = Pool()
        table1 = pool.get('party.party').__table__()
        table2 = pool.get('condo.payment.sepa.mandate').__table__()
        table3 = cls.__table__()

        #SELECT "c"."id" FROM "party_party" AS "a" INNER JOIN "condo_payment_sepa_mandate" AS "b"
        #              ON ("a"."id" = "b"."party") INNER JOIN "condo_payment" AS "c"
        #              ON ("b"."id" = "c"."sepa_mandate") WHERE (UPPER("a"."name") LIKE UPPER(?))
        query = table1.join(table2,
                        condition=table1.id == table2.party).join(table3,
                        condition=table2.id == table3.sepa_mandate).select(
                             table3.id,
                             where=Operator(table1.name, value))

        return [('id', 'in', query)]

    @staticmethod
    def order_debtor(tables):
        pool = Pool()
        Mandate = pool.get('condo.payment.sepa.mandate')

        field = Mandate._fields['party']
        table, _ = tables[None]
        mandate = Mandate.__table__()

        order_tables = {
                None: (mandate, mandate.id == table.sepa_mandate),
                }
        tables['sepa_mandate'] = order_tables

        return field.convert_order('party', order_tables, Mandate)

    @property
    def sequence_type(self):
        if self.type == 'one-off':
            return 'OOFF'
        elif self.type == 'first':
            return 'FRST'
        elif self.type == 'final':
            return 'FNAL'
        else:
            return 'RCUR'

    @classmethod
    def delete(cls, payments):
        for payment in payments:
            if payment.state != 'draft':
                cls.raise_user_error('delete_draft', (payment.rec_name))
        super(CondoPayment, cls).delete(payments)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, payments):
        for payment in payments:
            if payment.group:
                if payment.group.pain and payment.group.pain.state=='draft':
                    continue
            cls.raise_user_error('invalid_draft', (payment.group.pain.reference,
                                                   payment.party.name,
                                                   payment.company.party.name))

    @classmethod
    @ModelView.button
    @Workflow.transition('approved')
    def approve(cls, payments):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('succeeded')
    def succeed(cls, payments):
        for payment in payments:
            if payment.group:
                if payment.group.pain and payment.group.pain.state=='booked':
                    continue
            cls.raise_user_error('invalid_succeeded', (payment.group.pain.reference,
                                                       payment.party.name,
                                                       payment.company.party.name))

    @classmethod
    @ModelView.button
    @Workflow.transition('failed')
    def fail(cls, payments):
        pass


class CondoMandate(Workflow, ModelSQL, ModelView):
    'Condominium SEPA Mandate'
    __name__ = 'condo.payment.sepa.mandate'
    company = fields.Many2One('company.company', 'Condominium', required=True,
        select=True,
        domain=[
            ('party.active', '=', True),
            ('is_Condominium', '=', True)
            ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    party = fields.Many2One('party.party', 'Party', required=True, select=True,
        domain=[ 'OR',
                    ('categories', '=', None),
                    ('categories.name', '!=', 'bank')
            ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state','company'])
    condoparties = fields.One2Many('condo.party', 'sepa_mandate', 'Parties')
    account_number = fields.Many2One('bank.account.number', 'Account Number',
        ondelete='RESTRICT',
        states={
            'readonly': Eval('state') == 'canceled',
            'required': Eval('state') == 'validated',
            },
        domain=[
            ('type', '=', 'iban'),
            If(Bool(Eval('state') == 'canceled'),
                [ 'OR',
                    ('account.active', '=', True),
                    ('account.active', '=', False),
                ],
                [
                    ('account.active', '=', True),
                ],
            ),
            ('account.owners', If(Bool(Eval('party')), '=', '!='), Eval('party')),
            ],
        depends=['state', 'party'])
    identification = fields.Char('Identification', size=35,
        states={
            'readonly': Bool(Eval('has_payments')) == True,
            'required': Eval('state') == 'validated',
            },
        depends=['state', 'has_payments'])
    type = fields.Selection([
            ('recurrent', 'Recurrent'),
            ('one-off', 'One-off'),
            ], 'Type',
        states={
            'readonly': Eval('state').in_(['validated', 'canceled']),
            },
        depends=['state'])
    scheme = fields.Selection([
            ('CORE', 'Core'),
            ('B2B', 'Business to Business'),
            ], 'Scheme', required=True,
        states={
            'readonly': Eval('state').in_(['validated', 'canceled']),
            },
        depends=['state'])
    scheme_string = scheme.translated('scheme')
    signature_date = fields.Date('Signature Date',
        states={
            'readonly': Eval('state').in_(['validated', 'canceled']),
            'required': Eval('state') == 'validated',
            },
        depends=['state'])
    state = fields.Selection([
            ('draft', 'Draft'),
            ('requested', 'Requested'),
            ('validated', 'Validated'),
            ('canceled', 'Canceled'),
            ], 'State', readonly=True)
    payments = fields.One2Many('condo.payment', 'sepa_mandate', 'Payments')
    has_payments = fields.Function(fields.Boolean('Has Payments'),
        getter='get_has_payments')

    @classmethod
    def __setup__(cls):
        super(CondoMandate, cls).__setup__()
        cls._order.insert(0, ('identification', 'ASC'))
        t = cls.__table__()
        cls._transitions |= set((
                ('draft', 'requested'),
                ('requested', 'validated'),
                ('validated', 'canceled'),
                ('requested', 'canceled'),
                ('requested', 'draft'),
                ))
        cls._buttons.update({
                'cancel': {
                    'invisible': ~Eval('state').in_(
                        ['requested', 'validated']),
                    },
                'draft': {
                    'invisible': Eval('state') != 'requested',
                    },
                'request': {
                    'invisible': Eval('state') != 'draft',
                    },
                'validate_mandate': {
                    'invisible': Eval('state') != 'requested',
                    },
                })
        cls._sql_constraints = [
            ('identification_unique', Unique(t, t.company, t.identification),
                'The identification of the SEPA mandate must be unique '
                'in a company.'),
            ]
        cls._error_messages.update({
                'delete_draft_canceled': ('You can not delete mandate "%s" '
                    'because it is not in draft or canceled state or has payments.'),
                })
        cls._history = True

    @staticmethod
    def default_type():
        return 'recurrent'

    @staticmethod
    def default_scheme():
        return 'CORE'

    @staticmethod
    def default_state():
        return 'draft'

    def get_rec_name(self, name):
        return self.identification

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('identification',) + tuple(clause[1:])
            ]

    @staticmethod
    def order_company(tables):
        table, _ = tables[None]
        return [table.company, table.identification]

    @classmethod
    def validate(cls, mandates):
        super(CondoMandate,cls).validate(mandates)
        for mandate in mandates:
            mandate.validate_active()

    def validate_active(self):
        #Deactivate mandate as unit mandate on canceled state
        if (self.id > 0) and self.state=='canceled':
            condoparties = Pool().get('condo.party').__table__()
            cursor = Transaction().cursor

            cursor.execute(*condoparties.select(condoparties.id,
                                        where=(condoparties.sepa_mandate == self.id) &
                                              (condoparties.active == True)))

            ids = [ids for (ids,) in cursor.fetchall()]
            if len(ids):
                self.raise_user_warning('warn_canceled_mandate',
                    'Mandate "%s" will be canceled as mean of payment in %d unit(s)/apartment(s)!', (self.identification, len(ids)))

                for sub_ids in grouped_slice(ids):
                    red_sql = reduce_ids(condoparties.id, sub_ids)
                    # Use SQL to prevent double validate loop
                    cursor.execute(*condoparties.update(
                            columns=[condoparties.sepa_mandate],
                            values=[None],
                            where=red_sql))

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        args = []
        for mandates, values in zip(actions, actions):
            # Prevent raising false unique constraint
            if values.get('identification') == '':
                values = values.copy()
                values['identification'] = None
            args.extend((mandates, values))
        super(CondoMandate, cls).write(*args)

    @classmethod
    def copy(cls, mandates, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default.setdefault('state', 'draft')
        default.setdefault('payments', [])
        default.setdefault('signature_date', None)
        default.setdefault('identification', None)
        return super(CondoMandate, cls).copy(mandates, default=default)

    @classmethod
    def get_has_payments(cls, mandates, name):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().cursor

        has_payments = dict.fromkeys([m.id for m in mandates], False)

        for sub_ids in grouped_slice(mandates):
            red_sql = reduce_ids(payment.sepa_mandate, sub_ids)
            cursor.execute(*payment.select(payment.sepa_mandate, Literal(True),
                    where=red_sql,
                    group_by=payment.sepa_mandate))
            has_payments.update(cursor.fetchall())

        return has_payments

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, mandates):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('requested')
    def request(cls, mandates):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('validated')
    def validate_mandate(cls, mandates):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('canceled')
    def cancel(cls, mandates):
        # TODO must be automaticaly canceled 13 months after last collection
        pass

    @classmethod
    def delete(cls, mandates):
        for mandate in mandates:
            if ((mandate.state == 'draft')
               or (mandate.state == 'canceled' and not mandate.has_payments)):
                   continue
            cls.raise_user_error('delete_draft_canceled', mandate.rec_name)
        super(CondoMandate, cls).delete(mandates)


class CondoMandateReport(CompanyReport):
    __name__ = 'account.payment.sepa.mandate'
