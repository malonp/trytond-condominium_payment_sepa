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
from itertools import groupby

import unicodecsv
from cStringIO import StringIO
from decimal import Decimal

import genshi
import genshi.template
from sql import Literal

from trytond.pool import Pool
from trytond.model import ModelSQL, ModelView, Workflow, fields, dualmethod, Unique
from trytond.pyson import Eval, If, Not, Bool
from trytond.transaction import Transaction
from trytond.tools import reduce_ids, grouped_slice

from trytond.modules.company import CompanyReport


__all__ = ['CondoPain', 'CondoPaymentGroupPain', 'CondoPaymentGroup', 'CondoPayment', 'CondoMandate', 'CondoMandateReport']


class CondoPain(ModelSQL, ModelView):
    'Condominium Payment Initation Message'
    __name__ = 'condo.payment.pain'
    reference = fields.Char('Reference', required=True,
        states={
            'readonly': Eval('id', 0) > 0
            })
    company = fields.Many2One('company.company', 'Initiating Party',
        domain=[
            ('party.active', '=', True),
            ],
        select=True, required=True,
        states={
            'readonly': Eval('id', 0) > 0
            })
    sepa_receivable_flavor = fields.Selection([
            (None, ''),
            ('pain.008.001.02', 'pain.008.001.02'),
            ('pain.008.001.04', 'pain.008.001.04'),
            ], 'Receivable Flavor', required=True,
        translate=False)
    groups = fields.Many2Many('condo.payment.group-condo.payment.pain',
        'pain', 'group', 'Payments Groups')
    message = fields.Text('Message')

    @classmethod
    def __setup__(cls):
        super(CondoPain, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('reference_unique', Unique(t,t.company, t.reference),
                'The reference must be unique! for each party'),
        ]
        cls._buttons.update({
                'generate_message': {},
                })

    def sepa_group_payment_key(self, payment):
        key = (('date', payment.date),)
        key += (('group', payment.group),)
        key += (('sequence_type', payment.sepa_mandate_sequence_type),)
        key += (('scheme', payment.sepa_mandate.scheme),)
        return key

    @property
    def sepa_payments(self):
        keyfunc = self.sepa_group_payment_key
        payments = sorted(self.groups.payments, key=keyfunc)
        for key, grouped_payments in groupby(payments, key=keyfunc):
            yield dict(key), list(grouped_payments)

    def get_sepa_template(self):
        if self.sepa_receivable_flavor:
            return loader.load('%s.xml' % self.sepa_receivable_flavor)

    @dualmethod
    @ModelView.button
    def generate_message(cls, pains, _save=True):
        pool = Pool()
        for pain in pains:
            tmpl = pain.get_sepa_template()
            for group in pain.groups:
                message = tmpl.generate(group=group,
                    datetime=datetime, normalize=unicodedata.normalize,
                    ).filter(remove_comment).render()
                if pain.message:
                    pain.message += message
                else:
                    pain.message = message
            pain.save()
#TODO


def remove_comment(stream):
    for kind, data, pos in stream:
        if kind is genshi.core.COMMENT:
            continue
        yield kind, data, pos


loader = genshi.template.TemplateLoader(
    os.path.join(os.path.dirname(__file__), 'template'),
    auto_reload=True)


class CondoPaymentGroupPain(ModelSQL):
    'Group - Pain'
    __name__ = 'condo.payment.group-condo.payment.pain'
    _table = 'condo_payment_group_pain'
    group = fields.Many2One('condo.payment.group', 'Payment Group', ondelete='CASCADE',
            required=True, select=True)
    pain = fields.Many2One('condo.payment.pain', 'Pain Message',
        ondelete='CASCADE', required=True, select=True)


class CondoPaymentGroup(ModelSQL, ModelView):
    'Condominium Payment Group'
    __name__ = 'condo.payment.group'
    _rec_name = 'reference'
    reference = fields.Char('Reference', required=True,
        states={
            'readonly': Eval('id', 0) > 0
            })
    company = fields.Many2One('company.company', 'Condominium',
        domain=[
            ('party.active', '=', True),
            ('is_Condominium', '=', True)
            ],
        select=True, required=True,
        states={
            'readonly': Eval('id', 0) > 0
            })
    pain = fields.Many2Many('condo.payment.group-condo.payment.pain',
        'group', 'pain', 'PAIN Messages')
    account_number = fields.Many2One('bank.account.number', 'Account Number',
        ondelete='RESTRICT',
        domain=[
            ('type', '=', 'iban'),
            ('account.active', '=', True),
            ('account.owners.companies', '=', Eval('company')),
            ],
        depends=['company'], required=True)
    date = fields.Date('Date', required=True)
    payments = fields.One2Many('condo.account.payment', 'group', 'Payments')
    sepa_batch_booking = fields.Boolean('Batch Booking')
    sepa_charge_bearer = fields.Selection([
            ('DEBT', 'Debtor'),
            ('CRED', 'Creditor'),
            ('SHAR', 'Shared'),
            ('SLEV', 'Service Level'),
            ], 'Charge Bearer', required=True)
    message = fields.Text('Message')

    @classmethod
    def __setup__(cls):
        super(CondoPaymentGroup, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('reference_unique', Unique(t,t.company, t.reference),
                'The reference must be unique! for each condominium'),
        ]
        cls._buttons.update({
                'generate_message': {},
                })

    @staticmethod
    def default_date():
        pool = Pool()
        Date = pool.get('ir.date')
        return Date.today()

    @staticmethod
    def default_sepa_batch_booking():
        return True

    @staticmethod
    def default_sepa_charge_bearer():
        return 'SLEV'

    @dualmethod
    @ModelView.button
    def generate_message(cls, groups, _save=True):
        pool = Pool()

        Units = pool.get('condo.unit')

        CondoParties = pool.get('condo.party')
        CondoPayments = pool.get('condo.account.payment')

        for group in groups:
            condoparties = CondoParties.search([('unit.company', '=', group.company),
                ('sepa_mandate', '!=', None),
                ], order=[('unit.name', 'ASC'),])

            for condoparty in condoparties:
                if ((condoparty.sepa_mandate.state not in ['draft', 'canceled']) and
                       (CondoPayments.search_count(
                               [('group', '=', group),
                                ('state', '=', 'draft'),
                                ('unit', '=', condoparty.unit),
                                ('party', '=', condoparty.party)])<1)):
                        condopayment = CondoPayments()
                        condopayment.group = group
                        condopayment.fee = True
                        condopayment.unit = condoparty.unit
                        #Set the condoparty as the party
                        #(instead the debtor of the mandate condoparty.sepa_mandate.party)
                        condopayment.party = condoparty.party
                        condopayment.sepa_mandate = condoparty.sepa_mandate
                        condopayment.date = group.date
                        condopayment.sepa_end_to_end_id = condoparty.unit.name
                        condopayment.save()

            if group.message:
                message = group.message.encode('utf-8')
                f = StringIO(message)
                r = unicodecsv.reader(f, delimiter=';', encoding='utf-8')
                row = r.next()
                while row:
                    payment = CondoPayments.search([('unit.name', '=', row[0]),
                                                    ('group', '=', group),
                                                   ])
                    if len(payment)>0:
                        payment[0].amount = Decimal(row[1].replace(",", "."))
                        payment[0].description = row[2]
                        payment[0].save()
                    try:
                        row = r.next()
                    except:
                        break


class CondoPayment(Workflow, ModelSQL, ModelView):
    'Condominium Payment'
    __name__ = 'condo.account.payment'
    group = fields.Many2One('condo.payment.group', 'Group',
        ondelete='RESTRICT', required=True,
        states={
            'readonly': Eval('id', 0) > 0
            })
    company = fields.Function(fields.Many2One('company.company',
            'Company'), 'on_change_with_company')
    fee = fields.Boolean('Fee', help="Check if this payment correspond to unit's fee",
       states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    unit = fields.Many2One('condo.unit', 'Unit',
        domain=[
            ('company.groups_payments', '=', Eval('group'))
            ],
        states={
            'required': Bool(Eval('fee')),
            'invisible': Not(Bool(Eval('fee')))
            },
        depends=['group', 'fee'])
    unit_name=fields.Function(fields.Char('Unit'),
        'get_unit_name', searcher='search_unit_name')
    party = fields.Many2One('party.party', 'Party', required=True,
        domain=[ If(Bool(Eval('fee')),
                       [
#This party is owner or tenant of the unit and have a mandate for it (on his own name or not)
                           ('units.sepa_mandate.company', '=', Eval('company')),
                           ('units.sepa_mandate.state', 'not in', ['draft', 'canceled']),
                           ('units.isactive', '=', True),
                           ('units.unit', '=', Eval('unit'))
                       ],
                       [
#Subcondominium of the condominium with a mandate on his own name
                           ('sepa_mandates.company', '=', Eval('company')),
                           ('sepa_mandates.state', 'not in', ['draft', 'canceled']),
                           ('companies.parent', 'child_of', [Eval('company')]),
                           ('companies', '!=', Eval('company'))
                       ]
                   )
               ],
        depends=['group', 'fee', 'company'])
    description = fields.Char('Description', size=140)
    currency = fields.Function(fields.Many2One('currency.currency',
            'Currency'), 'on_change_with_currency')
    currency_digits = fields.Function(fields.Integer('Currency Digits'),
        'on_change_with_currency_digits')
    amount = fields.Numeric('Amount',
        digits=(11, Eval('currency_digits', 2)), states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state', 'currency_digits'])
    sepa_mandate = fields.Many2One('condo.account.payment.sepa.mandate', 'Mandate',
        ondelete='RESTRICT',
        domain=[ ('state', 'not in', ['draft', 'canceled']),
                 ('company', '=', Eval('company', -1)),
                 If(Bool(Eval('fee')),
                       [
                           ('condoparties.party', '=', Eval('party', -1))
                       ],
                       [
                           ('party', '=', Eval('party', -1))
                       ]
                   )
            ],
        depends=['party', 'company'])
    debtor_name = fields.Function(fields.Char('Debtor'),
        'get_debtor_name', searcher='search_debtor_name')
    sequence_type = fields.Selection([
            ('recurrent', 'RCUR'),
            ('one-off', 'OOFF'),
            ('final', 'FNAL'),
            ('first', 'FRST'),
            ], 'Sequence Type',
        states={
            'readonly': Eval('state').in_(
                        ['processing', 'succeeded', 'failed'])
            },
        depends=['sepa_mandate', 'state'])
    sepa_mandate_sequence_type = fields.Char('Mandate Sequence Type',
        depends=['sepa_mandate'],
        readonly=True)
    sepa_end_to_end_id = fields.Char('SEPA End To End ID', size=35,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['unit', 'sepa_mandate', 'state'])
    date = fields.Date('Date', required=True,
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

    @staticmethod
    def default_fee():
        return True

    @staticmethod
    def default_sequence_type():
        return 'recurrent'

    @staticmethod
    def default_state():
        return 'draft'

    @fields.depends('group')
    def on_change_with_company(self, name=None):
        if self.group:
            return self.group.company.id

    @fields.depends('group')
    def on_change_with_currency(self, name=None):
        if self.group:
            return self.group.company.currency.id

    @fields.depends('group')
    def on_change_with_currency_digits(self, name=None):
        if self.group:
            return self.group.company.currency.digits
        return 2

    @fields.depends('group')
    def on_change_with_date(self, name=None):
        if self.group:
            return self.group.date

    @fields.depends('sequence_type')
    def on_change_with_sepa_mandate_sequence_type(self, name=None):
        if self.sequence_type:
            if self.sequene_type == 'one-off':
                return 'OOFF'
#            elif (not self.payments
#                or all(not p.sepa_mandate_sequence_type for p in self.payments)
#                or all(p.rejected for p in self.payments)):
#            return 'FRST'
        # TODO manage FNAL
            else:
                return 'RCUR'

    @fields.depends('unit', 'sepa_mandate')
    def on_change_with_sepa_end_to_end_id(self, name=None):
        if self.unit:
            return self.unit.name
        return self.sepa_mandate.identification

    def get_unit_name(self, name):
        if self.unit:
            return self.unit.name

    @classmethod
    def search_unit_name(cls, name, domain):
        table = cls.__table__()
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]
        pool = Pool()

        table1 = pool.get('condo.unit').__table__()
        query1 = table1.select(table1.id,
            where=Operator(table1.name, value))

        query = table.select(table.id,
            where=(table.unit.in_(query1)))
        return [('id', 'in', query)]

    def get_debtor_name(self, name):
        if self.sepa_mandate:
            return self.sepa_mandate.party.name

    @classmethod
    def search_debtor_name(cls, name, domain):
        table = cls.__table__()
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]
        pool = Pool()

        table1 = pool.get('party.party').__table__()
        query1 = table1.select(table1.id,
            where=Operator(table1.name, value))

        table2 = pool.get('condo.account.payment.sepa.mandate').__table__()
        query2 = table2.select(table2.id,
            where=(table2.party.in_(query1)))

        query = table.select(table.id,
            where=(table.sepa_mandate.in_(query2)))
        return [('id', 'in', query)]

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
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('approved')
    def approve(cls, payments):
        pass

#    @classmethod
#    @Workflow.transition('processing')
#    def process(cls, payments, group):
#        pool = Pool()
#        Group = pool.get('account.payment.group')
#        if payments:
#            group = group()
#            cls.write(payments, {
#                    'group': group.id,
#                    })
#            process_method = getattr(Group,
#                'process_%s' % group.journal.process_method, None)
#            if process_method:
#                process_method(group)
#                group.save()
#            return group

    @classmethod
    @ModelView.button
    @Workflow.transition('succeeded')
    def succeed(cls, payments):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('failed')
    def fail(cls, payments):
        pass


class CondoMandate(Workflow, ModelSQL, ModelView):
    'Condominium SEPA Mandate'
    __name__ = 'condo.account.payment.sepa.mandate'
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
    condoparties = fields.One2Many('condo.party', 'sepa_mandate', 'Units/Apartments')
    account_number = fields.Many2One('bank.account.number', 'Account Number',
        ondelete='RESTRICT',
        states={
            'readonly': Eval('state') == 'canceled',
            'required': Eval('state') == 'validated',
            },
        domain=[
            ('type', '=', 'iban'),
            ('account.active', '=', True),
            ('account.owners', '=', Eval('party')),
            ],
        depends=['state', 'party'])
    identification = fields.Char('Identification', size=35,
        states={
            'readonly': Eval('identification_readonly', True),
            'required': Eval('state') == 'validated',
            },
        depends=['state', 'identification_readonly'])
    identification_readonly = fields.Function(fields.Boolean(
            'Identification Readonly'), 'get_identification_readonly')
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
    payments = fields.One2Many('condo.account.payment', 'sepa_mandate', 'Payments')
    has_payments = fields.Function(fields.Boolean('Has Payments'),
        'has_payments')

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
                    'because it is not in draft or canceled state.'),
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

#TODO
    def get_identification_readonly(self, name):
        return False

    def get_rec_name(self, name):
        return self.identification

#    @classmethod
#    def create(cls, vlist):
#        pool = Pool()
#        Sequence = pool.get('ir.sequence')
#        Configuration = pool.get('account.configuration')

#        config = Configuration(1)
#        vlist = [v.copy() for v in vlist]
#        for values in vlist:
#            if (config.sepa_condomandate_sequence
#                    and not values.get('identification')):
#                values['identification'] = Sequence.get_id(
#                    config.sepa_condomandate_sequence.id)
#            # Prevent raising false unique constraint
#            if values.get('identification') == '':
#                values['identification'] = None
#        return super(CondoMandate, cls).create(vlist)

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

    @property
    def is_valid(self):
        if self.state == 'validated':
            if self.type == 'one-off':
                if not self.has_payments:
                    return True
            else:
                return True
        return False

    @property
    def sequence_type(self):
        if self.type == 'one-off':
            return 'OOFF'
#        elif (not self.payments
#                or all(not p.sepa_mandate_sequence_type for p in self.payments)
#                or all(p.rejected for p in self.payments)):
#            return 'FRST'
        # TODO manage FNAL
        else:
            return 'RCUR'

    @classmethod
    def has_payments(self, mandates, name):
#        pool = Pool()
#        Payment = pool.get('condo.payment')
#        payment = Payment.__table__
#        cursor = Transaction().cursor

#        has_payments = dict.fromkeys([m.id for m in mandates], False)
#        for sub_ids in grouped_slice(mandates):
#            red_sql = reduce_ids(payment.sepa_mandate, sub_ids)
#            cursor.execute(*payment.select(payment.sepa_mandate, Literal(True),
#                    where=red_sql,
#                    group_by=payment.sepa_mandate))
#            has_payments.update(cursor.fetchall())

#        return {'has_payments': has_payments}
        return True

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
            if mandate.state not in ('draft', 'canceled'):
                cls.raise_user_error('delete_draft_canceled', mandate.rec_name)
        super(CondoMandate, cls).delete(mandates)


class CondoMandateReport(CompanyReport):
    __name__ = 'account.payment.sepa.mandate'
