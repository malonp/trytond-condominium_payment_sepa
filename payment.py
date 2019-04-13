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

import genshi
import genshi.template
from sql import Column, Literal
from sql.aggregate import Count, Max

from trytond.pool import Pool
from trytond.model import ModelSQL, ModelView, Workflow, fields, dualmethod, Unique
from trytond.pyson import Eval, If, Not, Bool
from trytond.transaction import Transaction
from trytond.tools import reduce_ids, grouped_slice

from trytond.modules.company import CompanyReport

from . import sepadecode
EPC_COUNTRIES = list(sepadecode._countries)


__all__ = ['CondoPain', 'CondoPaymentGroup',
           'CondoPayment', 'CondoMandate', 'CondoMandateReport',
           ]


class CondoPain(Workflow, ModelSQL, ModelView):
    'Condominium Payment Initation Message'
    __name__ = 'condo.payment.pain'
    reference = fields.Char('Reference', required=True,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    bank = fields.Function(fields.Many2One('bank', 'Creditor Agent'),
        'on_change_with_bank')
    company = fields.Many2One('company.company', 'Initiating Party',
        domain=[('party.active', '=', True),],
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
        add_remove=[('OR', [('pain', '=', None),
                        If(Bool(Eval('company')), ['OR', ('company', '=', Eval('company')), ('company.parent', 'child_of', Eval('company'))], []),],
                        ('pain', '=', Eval('id', -1)),),
                    If(Bool(Eval('bank')), [('account_number.account.bank', '=', Eval('bank'))], []),],     #Implies one unique Agent Creditor (bank) per message
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['bank', 'company', 'state'])
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
    subset = fields.Boolean('ASCII ISO20022',
        help=('Use Unicode Character Subset defined in ISO20022 for SEPA schemes'),
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    country_subset = fields.Many2One('country.country', 'Extended Character Set',
            depends=['groups', 'subset'], domain=[('code', 'in', EPC_COUNTRIES),],
            help=('Country Extended Character Set'),
            states={
                'readonly': Eval('state') != 'draft',
                'invisible': Not(Bool(Eval('subset'))),
            })
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

    @staticmethod
    def default_subset():
        return False

    @fields.depends('groups')
    def on_change_with_bank(self, name=None):
        if self.groups:
            bank = None
            for g in self.groups:
                if not bank:
                    bank = g.account_number.account.bank.id
                elif bank != g.account_number.account.bank.id:
                    bank = None
                    break
            return bank

    @fields.depends('groups')
    def on_change_groups(self):
        if self.groups:
            bank = None
            for g in self.groups:
                if not bank:
                    bank = g.account_number.account.bank
                elif bank.id != g.account_number.account.bank.id:
                    bank = None
                    break

            if bank:
                self.bank = bank
                self.subset = bank.subset
                self.country_subset = bank.country_subset

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
        cursor = Transaction().connection.cursor()

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
        cursor = Transaction().connection.cursor()

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
                    datetime=datetime, normalize=sepadecode.sepa_conversion,
                    ).filter(remove_comment).render()
                pain.message = message

                pain.save()
            except:
                Transaction().rollback()
                cls.raise_user_error('generate_error', (pain.reference,pain.company.party.name))
            else:
                Transaction().commit()

    @classmethod
    @ModelView.button
    @Workflow.transition('booked')
    def accept(cls, pains):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().connection.cursor()

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
        cursor = Transaction().connection.cursor()

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
                ('is_Condominium', '=', True),
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
                If(Bool(Eval('company')), [('account.owners.companies', '=', Eval('company')),], []),
                ('account.owners.companies.is_Condominium', '=', True),
                If(Bool(Eval('readonly')), [], [('account.active', '=', True),]),
                ('type', '=', 'iban'),
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
            ], 'Charge Bearer', required=True, sort=False,
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

    @staticmethod
    def default_sepa_batch_booking():
        Configuration = Pool().get('condo.payment.group.configuration')
        config = Configuration(1)
        if config.sepa_batch_booking_selection == '1':
            return True
        elif config.sepa_batch_booking_selection == '0':
            return False

    @staticmethod
    def default_sepa_charge_bearer():
        Configuration = Pool().get('condo.payment.group.configuration')
        config = Configuration(1)
        if config.sepa_charge_bearer:
            return config.sepa_charge_bearer

    @classmethod
    def validate(cls, paymentgroups):
        super(CondoPaymentGroup, cls).validate(paymentgroups)

        table = cls.__table__()
        payments = Pool().get('condo.payment').__table__()

        for paymentgroup in paymentgroups:
            if paymentgroup.readonly:
                with Transaction().new_transaction(readonly=True) as transaction,\
                    transaction.connection.cursor() as cursor:
                    cursor.execute(*table.select(table.date,
                                 where=(table.id == paymentgroup.id) &
                                       (table.date != paymentgroup.date)))
                    if cursor.fetchone():
                        cls.raise_user_error('readonly_paymentgroup', (paymentgroup.reference)
                            )
                return

            cursor = Transaction().connection.cursor()
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

    @classmethod
    def order_company(cls, tables):
        table, _ = tables[None]
        return chain.from_iterable([ cls.company.convert_order('company.party.name', tables, cls),
                                     [Column(table, 'date')],
                                   ])

    @classmethod
    def order_reference(cls, tables):
        table, _ = tables[None]
        return chain.from_iterable([ [Column(table, 'reference')],
                                     cls.company.convert_order('company.party.name', tables, cls),
                                   ])


class CondoPayment(Workflow, ModelSQL, ModelView):
    'Condominium Payment'
    __name__ = 'condo.payment'
    group = fields.Many2One('condo.payment.group', 'Group',
        ondelete='RESTRICT', required=True,
        domain=[ If(Eval('state') == 'draft',
                    [('OR', ('pain', '=', None), ('pain.state' ,'=', 'draft')),
                     If(Bool(Eval('company')),                                                                              #defined at least one of 'sepa_mandate', 'unit'
                        [('company.parent', 'parent_of', Eval('company')),],
                        [If(Bool(Eval('unit')), [('company.condo_units', '=', Eval('unit')),], []),]),                      #should not happend
                     #If(Bool(Eval('party')), [('company.condo_units.parties.party', '=', Eval('party'))], []),             #can't catch group of parents (TODO)
                     If(Bool(Eval('sepa_mandate')), [('company.sepa_mandates', '=', Eval('sepa_mandate'))], []),],          #sepa_mandate => group
                    []),
               ],
        states={
            'readonly': Eval('id', 0) > 0
            }, depends=['state'])
    company = fields.Function(fields.Many2One('company.company', 'Company'),
        getter='on_change_with_company', searcher='search_company')
    unit = fields.Many2One('condo.unit', 'Unit',
        domain=[ If(Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                    [],
                    [If(Bool(Eval('company')),                                                                              #defined at least one of 'group', 'sepa_mandate'
                        ['OR', ('company', '=', Eval('company')), ('company.parent', 'child_of', Eval('company')),],
                        [If(Bool(Eval('group')), [('company.groups_payments', '=', Eval('group')),], []),                   #should not happend
                         If(Bool(Eval('sepa_mandate')), [('company.sepa_mandates', '=', Eval('sepa_mandate')),], []),]),    #should not happend
                     If(Bool(Eval('party')), [('parties.party', '=', Eval('party')),], []),]),                              #party => unit
               ],
        states={
            'readonly': Eval('state') != 'draft',
            }, depends=['group', 'party', 'sepa_mandate', 'state']
        )
    unit_name=fields.Function(fields.Char('Unit'),
        getter='get_unit_name', searcher='search_unit_name')
    party = fields.Many2One('party.party', 'Ultimate Debtor', required=True,
        domain=[ If(Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                    [],
                    [   #'company' is function field so this won't work unless define method on_change_with_company
                        # that client will call when user changes one of the fields defined in the list @fields.depends
                        If(Bool(Eval('company')),                                                                                                               #defined at least one of 'group', 'sepa_mandate', 'unit'
                            ['OR', ('units.sepa_mandate.company', '=', Eval('company')), ('units.sepa_mandate.company.parent', 'child_of', Eval('company'))],
                            [If(Bool(Eval('group')), [('units.sepa_mandate.company.groups_payments', '=', Eval('group')),], []),                                #should not happend
                             If(Bool(Eval('sepa_mandate')), [('sepa_mandates.company.sepa_mandates', '=', Eval('sepa_mandate'))], []),]),                       #should not happend
                        If(Bool(Eval('unit')),                                                                                                                  #unit => party
                            [('units.unit', '=', Eval('unit')), ('units.sepa_mandate.state', 'not in', ['draft', 'canceled']),],
                            []),]),
               ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['company', 'group', 'state', 'unit'])
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
                     [],
                     [If(Bool(Eval('company')),                                                                 #defined at least one of 'group', 'unit'
                        [('company.parent', 'parent_of', Eval('company')),],
                        [If(Bool(Eval('unit')), [('company.condo_units', '=', Eval('unit')),], []),]),          #should not happend
                      #If(Bool(Eval('party')), [('company.condo_units.parties.party', '=', Eval('party'))], []),#can't catch sepa_mandates of parents (TODO)
                      If(Bool(Eval('group')), [('company.groups_payments', '=', Eval('group')),], []),          #group => sepa_mandate
                      ('state', 'not in', ['draft', 'canceled']),]),
               ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['group', 'party', 'state', 'unit'])
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
                    'icon': 'tryton-back',
                    },
                'approve': {
                    'invisible': Eval('state') != 'draft',
                    'icon': 'tryton-forward',
                    },
                'processing': {
                    'icon': 'tryton-launch',
                    },
                'succeed': {
                    'invisible': ~Eval('state').in_(['processing', 'failed']),
                    'icon': 'tryton-ok',
                    },
                'fail': {
                    'invisible': ~Eval('state').in_(['processing', 'succeeded']),
                    'icon': 'tryton-cancel',
                    },
                })

    @classmethod
    def validate(cls, payments):
        super(CondoPayment, cls).validate(payments)

        table = cls.__table__()

        for payment in payments:
            if payment.state != 'draft':
                with Transaction().new_transaction(readonly=True) as transaction,\
                    transaction.connection.cursor() as cursor:
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
            payment.check_xml_characters()

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

    def check_xml_characters(self):
        for f in [self.description, self.sepa_end_to_end_id]:
            if not f:
                return
            elif '//' in f:
                self.raise_user_error(
                    "Data elements can't contain 2 consecutive '/'")
            elif f.startswith('/') or f.endswith('/'):
                self.raise_user_error(
                    "Data elements can't start or end with '/' character")

    @staticmethod
    def default_state():
        return 'draft'

    @fields.depends('group')
    def on_change_group(self):
        if self.group:
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

    @fields.depends('group', 'sepa_mandate', 'unit')
    def on_change_with_company(self, name=None):
        if self.group:
            return self.group.company.id
        elif self.sepa_mandate:
            return self.sepa_mandate.company.id
        elif self.unit:
            return self.unit.company.id

    @fields.depends('sepa_mandate', 'unit')
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
        return chain.from_iterable([ cls.unit.convert_order('unit.name', tables, cls),
                                     cls.unit.convert_order('unit.company.party.name', tables, cls)
                                   ])

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

    @classmethod
    def order_company(cls, tables):
        return chain.from_iterable([ cls.unit.convert_order('unit.company.party.name', tables, cls),
                                     cls.unit.convert_order('unit.name', tables, cls)
                                   ])

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

    @classmethod
    def order_debtor(cls, tables):
        return cls.sepa_mandate.convert_order('sepa_mandate.party.name', tables, cls)

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
        domain=[('party.active', '=', True),
                ('is_Condominium', '=', True)],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    party = fields.Many2One('party.party', 'Party', required=True, select=True,
#        domain=['OR', ('categories', '=', None), ('categories.name', 'not in', ['bank']),],
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
        domain=[('type', '=', 'iban'),
                If(Bool(Eval('state') == 'canceled'),
                    ['OR', ('account.active', '=', True), ('account.active', '=', False),],
                    [('account.active', '=', True),],),
                If(Bool(Eval('party')), [('account.owners', '=', Eval('party')),], []),],
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
                    'invisible': ~Eval('state').in_(['requested', 'validated']),
                    'icon': 'tryton-cancel',
                    },
                'draft': {
                    'invisible': Eval('state') != 'requested',
                    },
                'request': {
                    'invisible': Eval('state') != 'draft',
                    },
                'validate_mandate': {
                    'invisible': Eval('state') != 'requested',
                    'icon': 'tryton-ok',
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

    @staticmethod
    def default_type():
        Configuration = Pool().get('condo.payment.sepa.mandate.configuration')
        config = Configuration(1)
        if config.type:
            return config.type

    @staticmethod
    def default_scheme():
        Configuration = Pool().get('condo.payment.sepa.mandate.configuration')
        config = Configuration(1)
        if config.scheme:
            return config.scheme

    @staticmethod
    def default_state():
        return 'draft'

    def get_rec_name(self, name):
        return self.identification

    @classmethod
    def search_rec_name(cls, name, clause):
        return [('identification',) + tuple(clause[1:])
            ]

    @classmethod
    def order_company(cls, tables):
        table, _ = tables[None]
        return chain.from_iterable([ cls.company.convert_order('company.party.name', tables, cls),
                                     [Column(table, 'identification')],
                                   ])

    @classmethod
    def validate(cls, mandates):
        super(CondoMandate,cls).validate(mandates)
        for mandate in mandates:
            mandate.validate_active()
            mandate.check_xml_characters()

    def validate_active(self):
        #Deactivate mandate as unit mandate on canceled state
        if (self.id > 0) and self.state=='canceled':
            condoparties = Pool().get('condo.party').__table__()
            condopayments = Pool().get('condo.payment').__table__()
            cursor = Transaction().connection.cursor()

            cursor.execute(*condopayments.select(condopayments.id,
                                        where=(condopayments.sepa_mandate == self.id) & (
                                              (condopayments.state == 'draft') | (condopayments.state == 'approved')),
                                        ))
            ids = [ids for (ids,) in cursor.fetchall()]
            if len(ids):
                self.raise_user_error('Can\'t cancel mandate "%s".\nThere are %s payments in draft or approved state with this mandate!',
                                                              (self.identification, len(ids)))

            cursor.execute(*condoparties.select(condoparties.id,
                                        where=(condoparties.sepa_mandate == self.id)))
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

    def check_xml_characters(self):
        if self.identification and '//' in self.identification:
            self.raise_user_error(
                "Mandate Identification can't contain 2 consecutive '/'")
        if self.identification and \
            (self.identification.startswith('/') or self.identification.endswith('/')):
                self.raise_user_error(
                    "Mandate Identification can't start or end with '/' character")

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
        cursor = Transaction().connection.cursor()

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
