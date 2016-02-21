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


from sql import Literal

from trytond.pool import Pool
from trytond.model import ModelSQL, ModelView, Workflow, fields, Unique
from trytond.pyson import Eval, If
from trytond.transaction import Transaction
from trytond.tools import reduce_ids, grouped_slice

from trytond.modules.company import CompanyReport


__all__ = ['CondoMandate', 'CondoMandateReport', 'CondoPayment']

_STATES = {
    'readonly': Eval('state') != 'draft',
    }
_DEPENDS = ['state']

class CondoPayment(Workflow, ModelSQL, ModelView):
    'Condominium Payment'
    __name__ = 'condo.account.payment'
    company = fields.Many2One('company.company', 'Company', required=True,
        select=True, states=_STATES, domain=[
            ('id', If(Eval('context', {}).contains('company'), '=', '!='),
                Eval('context', {}).get('company', -1)),
            ],
        depends=_DEPENDS)
    party = fields.Many2One('party.party', 'Party', required=True,
        states=_STATES, depends=_DEPENDS)
    sepa_mandate = fields.Many2One('condo.account.payment.sepa.mandate', 'Mandate',
        ondelete='RESTRICT',
        domain=[
            ('party', '=', Eval('party', -1)),
            ('company', '=', Eval('company', -1)),
            ],
        depends=['party', 'company'])

class CondoMandate(Workflow, ModelSQL, ModelView):
    'Condominium SEPA Mandate'
    __name__ = 'condo.account.payment.sepa.mandate'
    company = fields.Many2One('company.company', 'Condominium', required=True,
        select=True,
        domain=[
            ('is_Condominium', '=', True)
            ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    party = fields.Many2One('party.party', 'Party', required=True, select=True,
        domain=[ 'OR', [
                    ('units.isactive', '=', True),
                    ('units.unit.company.parent', 'child_of', [Eval('company')]),
                ], [
                    ('companies.parent', 'child_of', [Eval('company')]),
                    ('companies', '!=', Eval('company')),
                ],
            ],
        states={
            'readonly': Eval('state').in_(
                ['requested', 'validated', 'canceled']),
            },
        depends=['state','company'])
    account_number = fields.Many2One('bank.account.number', 'Account Number',
        ondelete='RESTRICT',
        states={
            'readonly': Eval('state').in_(['validated', 'canceled']),
            'required': Eval('state') == 'validated',
            },
        domain=[
            ('type', '=', 'iban'),
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
        t = cls.__table__()
        cls._sql_constraints = [
            ('identification_unique', Unique(t, t.company, t.identification),
                'The identification of the SEPA mandate must be unique '
                'in a company.'),
            ]
        cls._error_messages.update({
                'delete_draft_canceled': ('You can not delete mandate "%s" '
                    'because it is not in draft or canceled state.'),
                })

#    @staticmethod
#    def default_company():
#        return Transaction().context.get('company')

    @staticmethod
    def default_type():
        return 'recurrent'

    @staticmethod
    def default_scheme():
        return 'CORE'

    @staticmethod
    def default_state():
        return 'draft'

#    @staticmethod
#    def default_identification_readonly():
#        pool = Pool()
#        Configuration = pool.get('account.configuration')
#        config = Configuration(1)
#        return bool(config.sepa_condomandate_sequence)

    def get_identification_readonly(self, name):
        return bool(self.identification)

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
#        default.setdefault('payments', [])
        default.setdefault('signature_date', None)
        default.setdefault('identification', None)
        return super(CondoMandate, cls).copy(mandates, default=default)

    @property
    def is_valid(self):
        if self.state == 'validated':
            if self.type == 'one-off':
#                if not self.has_payments:
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
