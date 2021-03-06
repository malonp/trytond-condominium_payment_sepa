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
from trytond.wizard import Wizard, StateTransition, StateView, Button

from trytond.modules.company import CompanyReport

from . import sepadecode

EPC_COUNTRIES = list(sepadecode._countries)


__all__ = ['CondoPain', 'Group', 'Payment', 'Mandate', 'MandateReport', 'CheckMandatesList', 'CheckMandates']

# XXX fix: https://genshi.edgewall.org/ticket/582
from genshi.template.astutil import ASTCodeGenerator, ASTTransformer

if not hasattr(ASTCodeGenerator, 'visit_NameConstant'):

    def visit_NameConstant(self, node):
        if node.value is None:
            self._write('None')
        elif node.value is True:
            self._write('True')
        elif node.value is False:
            self._write('False')
        else:
            raise Exception("Unknown NameConstant %r" % (node.value,))

    ASTCodeGenerator.visit_NameConstant = visit_NameConstant
if not hasattr(ASTTransformer, 'visit_NameConstant'):
    # Re-use visit_Name because _clone is deleted
    ASTTransformer.visit_NameConstant = ASTTransformer.visit_Name


class CondoPain(Workflow, ModelSQL, ModelView):
    'Condominium Payment Initation Message'
    __name__ = 'condo.payment.pain'
    reference = fields.Char(
        'Reference', depends=['state'], required=True, select=True, states={'readonly': Eval('state') != 'draft'}
    )
    bank = fields.Function(fields.Many2One('bank', 'Creditor Agent'), 'on_change_with_bank')
    company = fields.Many2One(
        'company.company',
        'Initiating Party',
        depends=['state'],
        domain=[('party.active', '=', True)],
        required=True,
        select=True,
        states={'readonly': Eval('state') != 'draft'},
    )
    sepa_receivable_flavor = fields.Selection(
        [
            # (None, ''),
            ('pain.008.001.02', 'pain.008.001.02'),
            # ('pain.008.001.04', 'pain.008.001.04'),
        ],
        'Receivable Flavor',
        depends=['state'],
        required=True,
        states={'readonly': Eval('state') != 'draft', 'required': Eval('state') != 'draft'},
        translate=False,
    )
    groups = fields.One2Many(
        'condo.payment.group',
        'pain',
        'Payments Groups',
        add_remove=[
            (
                'OR',
                [
                    ('pain', '=', None),
                    If(
                        Bool(Eval('company')),
                        ['OR', ('company', '=', Eval('company')), ('company.parent', 'child_of', Eval('company'))],
                        [],
                    ),
                ],
                ('pain', '=', Eval('id', -1)),
            ),
            # Next condition implies one unique Agent Creditor (bank) per message
            If(Bool(Eval('bank')), [('account_number.account.bank', '=', Eval('bank'))], []),
        ],
        depends=['bank', 'company', 'state'],
        states={'readonly': Eval('state') != 'draft'},
    )
    message = fields.Text('Message', states={'readonly': Eval('state') != 'draft'}, depends=['state'])
    state = fields.Selection(
        [('draft', 'Draft'), ('generated', 'Generated'), ('booked', 'Booked'), ('rejected', 'Rejected')],
        'State',
        select=True,
    )
    subset = fields.Boolean(
        'ASCII ISO20022',
        depends=['state'],
        help=('Use Unicode Character Subset defined in ISO20022 for SEPA schemes'),
        states={'readonly': Eval('state') != 'draft'},
    )
    country_subset = fields.Many2One(
        'country.country',
        'Extended Character Set',
        depends=['groups', 'subset'],
        domain=[('code', 'in', EPC_COUNTRIES)],
        help=('Country Extended Character Set'),
        states={'readonly': Eval('state') != 'draft', 'invisible': Not(Bool(Eval('subset')))},
    )
    nboftxs = fields.Function(fields.Integer('Number of Transactions'), 'get_nboftxs')
    ctrlsum = fields.Function(fields.Numeric('Control Sum', digits=(11, 2)), 'get_ctrlsum')

    @classmethod
    def __setup__(cls):
        super(CondoPain, cls).__setup__()
        cls._error_messages.update({'generate_error': ('Can not generate message "%s" of "%s"')})
        cls._transitions |= set(
            (
                ('draft', 'generated'),
                ('generated', 'draft'),
                ('generated', 'booked'),
                ('generated', 'rejected'),
                ('booked', 'rejected'),
                ('rejected', 'draft'),
            )
        )
        cls._buttons.update(
            {
                'cancel': {'invisible': ~Eval('state').in_(['generated', 'booked'])},
                'draft': {'invisible': ~Eval('state').in_(['generated', 'rejected'])},
                'generate': {'invisible': Eval('state') != 'draft'},
                'accept': {'invisible': Eval('state') != 'generated'},
            }
        )
        t = cls.__table__()
        cls._sql_constraints += [
            ('reference_unique', Unique(t, t.company, t.reference), 'The reference must be unique for each party!')
        ]

    @classmethod
    def validate(cls, pains):
        super(CondoPain, cls).validate(pains)
        for pain in pains:
            pain.company_has_sepa_creditor_identifier()

    def company_has_sepa_creditor_identifier(self):
        if not self.company.sepa_creditor_identifier:
            self.raise_user_error("Initiating Party must have a sepa creditor identifier")

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
            # http://stackoverflow.com/questions/952914/making-a-flat-list-out-of-list-of-lists-in-python?rq=1
            # leaf for tree in forest for leaf in tree
            return sum(1 for group in self.groups for p in group.payments if p.amount)

    def get_ctrlsum(self, name):
        if self.groups:
            return sum(p.amount for group in self.groups for p in group.payments if p.amount)

    def sepa_group_payment_key(self, payment):
        key = (('date', payment.date),)
        key += (('group', payment.group),)
        key += (('sequence_type', payment.sequence_type),)
        key += (('scheme', payment.mandate.scheme),)
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
            cursor.execute(*payment.update(columns=[payment.state], values=['draft'], where=red_sql))

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

                cursor.execute(*payment.update(columns=[payment.state], values=['approved'], where=red_sql))
            try:
                tmpl = pain.get_sepa_template()
                message = (
                    tmpl.generate(pain=pain, datetime=datetime, normalize=sepadecode.sepa_conversion)
                    .filter(remove_comment)
                    .render()
                )
                pain.message = message

                pain.save()
            except:
                Transaction().rollback()
                cls.raise_user_error('generate_error', (pain.reference, pain.company.party.name))
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
            cursor.execute(*payment.update(columns=[payment.state], values=['processing'], where=red_sql))

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
            cursor.execute(*payment.update(columns=[payment.state], values=['failed'], where=red_sql))


def remove_comment(stream):
    for kind, data, pos in stream:
        if kind is genshi.core.COMMENT:
            continue
        yield kind, data, pos


loader = genshi.template.TemplateLoader(os.path.join(os.path.dirname(__file__), 'template'), auto_reload=True)


class Group(ModelSQL, ModelView):
    'Condominium Payment Group'
    __name__ = 'condo.payment.group'
    _rec_name = 'reference'
    reference = fields.Char(
        'Reference', depends=['readonly'], required=True, select=True, states={'readonly': Bool(Eval('readonly'))}
    )
    company = fields.Many2One(
        'company.company',
        'Condominium',
        domain=[('party.active', '=', True), ('is_condo', '=', True)],
        required=True,
        select=True,
        states={'readonly': Eval('id', 0) > 0},
    )
    pain = fields.Many2One('condo.payment.pain', 'Pain Message', ondelete='SET NULL', select=True)
    account_number = fields.Many2One(
        'bank.account.number',
        'Account Number',
        depends=['company', 'readonly'],
        domain=[
            If(Bool(Eval('company')), [('account.owners.companies', '=', Eval('company'))], []),
            ('account.owners.companies.is_condo', '=', True),
            If(Bool(Eval('readonly')), [], [('account.active', '=', True)]),
            ('type', '=', 'iban'),
        ],
        ondelete='RESTRICT',
        required=True,
        states={'readonly': Bool(Eval('readonly'))},
    )
    date = fields.Date('Debit Date', required=True, states={'readonly': Bool(Eval('readonly'))}, depends=['readonly'])
    payments = fields.One2Many('condo.payment', 'group', 'Payments')
    sepa_batch_booking = fields.Boolean(
        'Batch Booking', states={'readonly': Bool(Eval('readonly'))}, depends=['readonly']
    )
    sepa_charge_bearer = fields.Selection(
        [('DEBT', 'Debtor'), ('CRED', 'Creditor'), ('SHAR', 'Shared'), ('SLEV', 'Service Level')],
        'Charge Bearer',
        depends=['readonly'],
        required=True,
        sort=False,
        states={'readonly': Bool(Eval('readonly'))},
    )
    nboftxs = fields.Function(fields.Integer('Number of Transactions'), 'get_nboftxs')
    ctrlsum = fields.Function(fields.Numeric('Control Sum', digits=(11, 2)), 'get_ctrlsum')
    readonly = fields.Function(fields.Boolean('State'), getter='get_readonly', searcher='search_readonly')

    @classmethod
    def __setup__(cls):
        super(Group, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            (
                'reference_unique',
                Unique(t, t.company, t.reference),
                'The reference must be unique for each condominium!',
            )
        ]
        cls._error_messages.update(
            {'payments_approved': ('PaymentGroup "%s" has payments approved' ' with earlier date.')}
        )

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
    def validate(cls, groups):
        super(Group, cls).validate(groups)

        payments = Pool().get('condo.payment').__table__()

        for group in groups:
            if group.readonly:
                continue

            group.check_today()
            group.check_businessdate()
            group.company_has_sepa_creditor_identifier()

            with Transaction().connection.cursor() as cursor:
                # there are approved payments with due date before new date
                # so raise user error
                cursor.execute(
                    *payments.select(
                        payments.id,
                        where=(payments.group == group.id) & (payments.date < group.date) & (payments.state != 'draft'),
                    )
                )
                if cursor.fetchall():
                    cls.raise_user_error('payments_approved', (group.reference))

                # there are drafted payments with due date before new date
                # update date field of payments
                cursor.execute(
                    *payments.select(
                        payments.id,
                        where=(payments.group == group.id) & (payments.date < group.date) & (payments.state == 'draft'),
                    )
                )
                ids_draft = [ids for (ids,) in cursor.fetchall()]

                if len(ids_draft):
                    for sub_ids in grouped_slice(ids_draft):
                        red_sql = reduce_ids(payments.id, sub_ids)
                        # Use SQL to prevent double validate loop
                        cursor.execute(*payments.update(columns=[payments.date], values=[group.date], where=red_sql))

    def check_today(self):
        if self.date:
            pool = Pool()
            Date = pool.get('ir.date')
            d = Date.today()
            if self.date < d:
                self.raise_user_error('Must select date after today!')

    def check_businessdate(self):
        if self.date and self.date.weekday() in (5, 6):
            self.raise_user_error("Date must be a business day!")

    def company_has_sepa_creditor_identifier(self):
        if not self.company.sepa_creditor_identifier:
            self.raise_user_error("Company must have a sepa creditor identifier")

    def get_nboftxs(self, name):
        if self.payments:
            # return len(self.payments)
            # we get only payments with valid amount
            return sum(1 for p in self.payments if p.amount)

    def get_ctrlsum(self, name):
        if self.payments:
            return sum(p.amount for p in self.payments if p.amount)

    def get_readonly(self, name):
        return self.pain.state != 'draft' if self.pain else False

    @classmethod
    def search_readonly(cls, name, domain):
        _, operator, value = domain
        pool = Pool()
        table1 = pool.get('condo.payment.pain').__table__()
        table2 = cls.__table__()

        if (operator == '=' and not value) or (operator == '!=' and value):
            query1 = table1.join(table2, condition=table1.id == table2.pain).select(
                table2.id, where=table1.state == 'draft'
            )
            query2 = table2.select(table2.id, where=table2.pain == None)
            return ['OR', ('id', 'in', query1), ('id', 'in', query2)]
        else:
            query = table1.join(table2, condition=table1.id == table2.pain).select(
                table2.id, where=table1.state != 'draft'
            )
            return [('id', 'in', query)]

    @classmethod
    def order_company(cls, tables):
        table, _ = tables[None]
        return chain.from_iterable(
            [cls.company.convert_order('company.party.name', tables, cls), [Column(table, 'date')]]
        )

    @classmethod
    def order_reference(cls, tables):
        table, _ = tables[None]
        return chain.from_iterable(
            [[Column(table, 'reference')], cls.company.convert_order('company.party.name', tables, cls)]
        )


class Payment(Workflow, ModelSQL, ModelView):
    'Condominium Payment'
    __name__ = 'condo.payment'
    group = fields.Many2One(
        'condo.payment.group',
        'Group',
        depends=['state'],
        domain=[
            If(
                Eval('state') == 'draft',
                [
                    ('OR', ('pain', '=', None), ('pain.state', '=', 'draft')),
                    # company field implies at least one of 'mandate', 'unit' are defined
                    If(Bool(Eval('company')), [('company.parent', 'parent_of', Eval('company'))], []),
                    # next one commented because can't catch group of parents (TODO)
                    # If(Bool(Eval('party')), [('company.units.condoparties.party', '=', Eval('party'))], []),
                    # mandate => group
                    If(Bool(Eval('mandate')), [('company.mandates', '=', Eval('mandate'))], []),
                    # restrict groups from condominiums with actives mandates
                    ('company.mandates.state', 'not in', ['draft', 'canceled']),
                ],
                [],
            )
        ],
        ondelete='RESTRICT',
        required=True,
        select=True,
        states={'readonly': Eval('id', 0) > 0},
    )
    company = fields.Function(
        fields.Many2One('company.company', 'Company'), getter='on_change_with_company', searcher='search_company'
    )
    unit = fields.Many2One(
        'condo.unit',
        'Unit',
        depends=['group', 'party', 'mandate', 'state'],
        domain=[
            If(
                Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                [],
                [
                    # company field implies at least one of 'group', 'mandate' are defined
                    If(
                        Bool(Eval('company')),
                        ['OR', ('company', '=', Eval('company')), ('company.parent', 'child_of', Eval('company'))],
                        [],
                    ),
                    # party => unit
                    If(Bool(Eval('party')), [('condoparties.party', '=', Eval('party'))], []),
                    # restrict to units with parties with actives mandates
                    ('condoparties.party.units.mandate.state', 'not in', ['draft', 'canceled']),
                ],
            )
        ],
        states={'readonly': Eval('state') != 'draft'},
    )
    unit_name = fields.Function(fields.Char('Unit'), getter='get_unit_name', searcher='search_unit_name')
    party = fields.Many2One(
        'party.party',
        'Ultimate Debtor',
        depends=['company', 'group', 'state', 'unit'],
        domain=[
            If(
                Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                [],
                [
                    # 'company' is function field so this won't work unless define method on_change_with_company
                    # that client will call when user changes one of the fields defined in the list @fields.depends
                    # company field implies at least one of 'group', 'mandate', 'unit' are defined
                    If(
                        Bool(Eval('company')),
                        [
                            'OR',
                            ('units.mandate.company', '=', Eval('company')),
                            ('units.mandate.company.parent', 'child_of', Eval('company')),
                        ],
                        [],
                    ),
                    # unit => party
                    If(Bool(Eval('unit')), [('units.unit', '=', Eval('unit'))], []),
                    # restrict to parties with actives mandates
                    ('units.mandate.state', 'not in', ['draft', 'canceled']),
                ],
            )
        ],
        required=True,
        states={'readonly': Eval('state') != 'draft'},
    )
    description = fields.Char('Description', size=140, states={'readonly': Eval('state') != 'draft'})
    currency = fields.Many2One(
        'currency.currency', 'Currency', states={'readonly': Eval('state') != 'draft'}, depends=['group']
    )
    currency_digits = fields.Function(fields.Integer('Currency Digits'), 'on_change_with_currency_digits')
    amount = fields.Numeric(
        'Amount',
        depends=['state', 'currency_digits'],
        digits=(11, Eval('currency_digits', 2)),
        states={'readonly': Eval('state') != 'draft', 'required': Eval('state') != 'draft'},
    )
    mandate = fields.Many2One(
        'condo.payment.sepa.mandate',
        'Mandate',
        depends=['group', 'party', 'state', 'unit'],
        domain=[
            If(
                Bool(Eval('state').in_(['processing', 'succeeded', 'failed'])),
                [],
                [
                    # company field implies at least one of 'group', 'unit' are defined
                    If(Bool(Eval('company')), [('company.parent', 'parent_of', Eval('company'))], []),
                    # Next one commented because can't catch mandates of parents (TODO)
                    # If(Bool(Eval('party')), [('company.units.condoparties.party', '=', Eval('party'))], []),
                    # group => mandate
                    If(Bool(Eval('group')), [('company.groups', '=', Eval('group'))], []),
                    ('state', 'not in', ['draft', 'canceled']),
                ],
            )
        ],
        ondelete='RESTRICT',
        required=True,
        states={'readonly': Eval('state') != 'draft'},
    )
    debtor = fields.Function(fields.Char('Debtor'), getter='get_debtor', searcher='search_debtor')
    type = fields.Selection(
        [('recurrent', 'RCUR'), ('one-off', 'OOFF'), ('final', 'FNAL'), ('first', 'FRST')],
        'Sequence Type',
        depends=['mandate', 'state'],
        required=True,
        states={'readonly': Eval('state').in_(['processing', 'succeeded', 'failed'])},
    )
    sepa_end_to_end_id = fields.Char(
        'SEPA End To End ID',
        depends=['unit', 'mandate', 'state'],
        size=35,
        states={'readonly': Eval('state') != 'draft'},
    )
    date = fields.Date(
        'Debit Date', required=True, states={'readonly': Eval('state') != 'draft'}, depends=['group', 'state']
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('approved', 'Approved'),
            ('processing', 'Processing'),
            ('succeeded', 'Succeeded'),
            ('failed', 'Failed'),
        ],
        'State',
        select=True,
    )

    @classmethod
    def __setup__(cls):
        super(Payment, cls).__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('unit.name', 'ASC'))
        cls._error_messages.update(
            {
                'delete_draft': ('Payment "%s" must be in draft before ' 'deletion.'),
                'invalid_mandate': ('Payment of "%s" has an invalid' ' mandate "%s"'),
                'invalid_draft': ('Message "%s" must be in draft to put' ' payment of "%s" to "%s" in draft too.'),
                'invalid_succeeded': (
                    'Message "%s" must be booked to put' ' payment of "%s" to "%s" in succeeded state.'
                ),
            }
        )
        cls._transitions |= set(
            (
                ('draft', 'approved'),
                ('approved', 'processing'),
                ('processing', 'succeeded'),
                ('processing', 'failed'),
                ('approved', 'draft'),
                ('succeeded', 'failed'),
                ('failed', 'succeeded'),
            )
        )
        cls._buttons.update(
            {
                'draft': {'invisible': Eval('state') != 'approved', 'icon': 'tryton-back'},
                'approve': {'invisible': Eval('state') != 'draft', 'icon': 'tryton-forward'},
                'processing': {'icon': 'tryton-launch'},
                'succeed': {'invisible': ~Eval('state').in_(['processing', 'failed']), 'icon': 'tryton-ok'},
                'fail': {'invisible': ~Eval('state').in_(['processing', 'succeeded']), 'icon': 'tryton-cancel'},
            }
        )

    @classmethod
    def validate(cls, payments):
        super(Payment, cls).validate(payments)

        for payment in payments:
            if payment.state == 'draft':
                payment.check_duedate()
                payment.check_businessdate()
                payment.check_account_number()
                payment.check_xml_characters()

    def check_duedate(self):
        if self.group and self.group.date:
            if self.date < self.group.date:
                self.raise_user_error("Fee due date must be equal or bigger than his group date")

    def check_businessdate(self):
        if self.date and self.date.weekday() in (5, 6):
            self.raise_user_error("Date must be a business day!")

    def check_account_number(self):
        if not self.mandate.account_number:
            self.raise_user_error('invalid_mandate', (self.party.name, self.mandate.identification))

    def check_xml_characters(self):
        for f in [self.description, self.sepa_end_to_end_id]:
            if not f:
                return
            elif '//' in f:
                self.raise_user_error("Data elements can't contain 2 consecutive '/'")
            elif f.startswith('/') or f.endswith('/'):
                self.raise_user_error("Data elements can't start or end with '/' character")

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

    @fields.depends('mandate')
    def on_change_mandate(self):
        if self.mandate:
            self.type = self.mandate.type
        else:
            self.type = None
            self.debtor = ''

    @fields.depends('group', 'mandate', 'unit')
    def on_change_with_company(self, name=None):
        if self.group:
            return self.group.company.id
        elif self.mandate:
            return self.mandate.company.id
        elif self.unit:
            return self.unit.company.id

    @fields.depends('mandate', 'unit')
    def on_change_with_sepa_end_to_end_id(self, name=None):
        if self.unit:
            return self.unit.name
        elif self.mandate:
            return self.mandate.identification

    @classmethod
    def get_unit_name(cls, payments, name):
        return dict([(p.id, p.unit.name if p.unit else None) for p in payments])

    @classmethod
    def search_unit_name(cls, name, domain):
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]

        pool = Pool()
        table1 = pool.get('condo.unit').__table__()
        table2 = cls.__table__()

        query = table1.join(table2, condition=table1.id == table2.unit).select(
            table2.id, where=Operator(table1.name, value)
        )

        return [('id', 'in', query)]

    @classmethod
    def order_unit_name(cls, tables):
        return chain.from_iterable(
            [
                cls.unit.convert_order('unit.name', tables, cls),
                cls.unit.convert_order('unit.company.party.name', tables, cls),
            ]
        )

    @classmethod
    def search_company(cls, name, domain):
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]

        pool = Pool()
        table1 = pool.get('party.party').__table__()
        table2 = pool.get('company.company').__table__()
        table3 = pool.get('condo.payment.group').__table__()
        table4 = cls.__table__()

        query = (
            table1.join(table2, condition=table1.id == table2.party)
            .join(table3, condition=table2.id == table3.company)
            .join(table4, condition=table3.id == table4.group)
            .select(table4.id, where=Operator(table1.name, value))
        )

        return [('id', 'in', query)]

    @classmethod
    def order_company(cls, tables):
        return chain.from_iterable(
            [
                cls.unit.convert_order('unit.company.party.name', tables, cls),
                cls.unit.convert_order('unit.name', tables, cls),
            ]
        )

    @classmethod
    def get_debtor(cls, payments, name):
        return dict([(p.id, p.mandate.party.name if p.mandate else None) for p in payments])

    @classmethod
    def search_debtor(cls, name, domain):
        _, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]

        pool = Pool()
        table1 = pool.get('party.party').__table__()
        table2 = pool.get('condo.payment.sepa.mandate').__table__()
        table3 = cls.__table__()

        # SELECT "c"."id" FROM "party_party" AS "a" INNER JOIN "condo_payment_sepa_mandate" AS "b"
        #              ON ("a"."id" = "b"."party") INNER JOIN "condo_payment" AS "c"
        #              ON ("b"."id" = "c"."mandate") WHERE (UPPER("a"."name") LIKE UPPER(?))
        query = (
            table1.join(table2, condition=table1.id == table2.party)
            .join(table3, condition=table2.id == table3.mandate)
            .select(table3.id, where=Operator(table1.name, value))
        )

        return [('id', 'in', query)]

    @classmethod
    def order_debtor(cls, tables):
        return cls.mandate.convert_order('mandate.party.name', tables, cls)

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
        super(Payment, cls).delete(payments)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, payments):
        for payment in payments:
            if payment.group:
                if not payment.group.pain:
                    continue
                elif payment.group.pain.state == 'draft':
                    continue
                else:
                    cls.raise_user_error(
                        'invalid_draft', (payment.group.pain.reference, payment.party.name, payment.company.party.name)
                    )

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
                if payment.group.pain and payment.group.pain.state == 'booked':
                    continue
            cls.raise_user_error(
                'invalid_succeeded', (payment.group.pain.reference, payment.party.name, payment.company.party.name)
            )

    @classmethod
    @ModelView.button
    @Workflow.transition('failed')
    def fail(cls, payments):
        pass


class Mandate(Workflow, ModelSQL, ModelView):
    'Condominium SEPA Mandate'
    __name__ = 'condo.payment.sepa.mandate'
    company = fields.Many2One(
        'company.company',
        'Condominium',
        depends=['state'],
        domain=[('party.active', '=', True), ('is_condo', '=', True)],
        required=True,
        select=True,
        states={'readonly': Eval('state') != 'draft'},
    )
    party = fields.Many2One(
        'party.party',
        'Party',
        depends=['state', 'company'],
        # domain=['OR', ('categories', '=', None), ('categories.name', 'not in', ['bank']),],
        required=True,
        select=True,
        states={'readonly': Eval('state') != 'draft'},
    )
    condoparties = fields.One2Many('condo.party', 'mandate', 'Parties')
    account_number = fields.Many2One(
        'bank.account.number',
        'Account Number',
        depends=['state', 'party'],
        domain=[
            ('type', '=', 'iban'),
            If(
                Bool(Eval('state') == 'canceled'),
                ['OR', ('account.active', '=', True), ('account.active', '=', False)],
                [('account.active', '=', True)],
            ),
            If(Bool(Eval('party')), [('account.owners', '=', Eval('party'))], []),
        ],
        ondelete='RESTRICT',
        states={'readonly': Eval('state') == 'canceled', 'required': Eval('state') == 'validated'},
    )
    identification = fields.Char(
        'Identification',
        depends=['state', 'has_payments'],
        size=35,
        states={'readonly': Bool(Eval('has_payments')) == True, 'required': Eval('state') == 'validated'},
    )
    type = fields.Selection(
        [('recurrent', 'Recurrent'), ('one-off', 'One-off')],
        'Type',
        depends=['state'],
        states={'readonly': Eval('state').in_(['validated', 'canceled'])},
    )
    scheme = fields.Selection(
        [('CORE', 'Core'), ('B2B', 'Business to Business')],
        'Scheme',
        depends=['state'],
        required=True,
        states={'readonly': Eval('state').in_(['validated', 'canceled'])},
    )
    scheme_string = scheme.translated('scheme')
    signature_date = fields.Date(
        'Signature Date',
        depends=['state'],
        states={'readonly': Eval('state').in_(['validated', 'canceled']), 'required': Eval('state') == 'validated'},
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('requested', 'Requested'), ('validated', 'Validated'), ('canceled', 'Canceled')],
        'State',
        select=True,
    )
    payments = fields.One2Many('condo.payment', 'mandate', 'Payments')
    has_payments = fields.Function(fields.Boolean('Has Payments'), getter='get_has_payments')

    @classmethod
    def __setup__(cls):
        super(Mandate, cls).__setup__()
        cls._order.insert(0, ('identification', 'ASC'))
        t = cls.__table__()
        cls._transitions |= set(
            (
                ('draft', 'requested'),
                ('requested', 'validated'),
                ('validated', 'canceled'),
                ('requested', 'canceled'),
                ('requested', 'draft'),
            )
        )
        cls._buttons.update(
            {
                'cancel': {'invisible': ~Eval('state').in_(['requested', 'validated']), 'icon': 'tryton-cancel'},
                'draft': {'invisible': Eval('state') != 'requested'},
                'request': {'invisible': Eval('state') != 'draft'},
                'validate_mandate': {'invisible': Eval('state') != 'requested', 'icon': 'tryton-ok'},
            }
        )
        cls._sql_constraints = [
            (
                'identification_unique',
                Unique(t, t.company, t.identification),
                'The identification of the SEPA mandate must be unique ' 'in a company.',
            )
        ]
        cls._error_messages.update(
            {
                'delete_draft_canceled': (
                    'You can not delete mandate "%s" ' 'because it is not in draft or canceled state or has payments.'
                )
            }
        )

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
        return [('identification',) + tuple(clause[1:])]

    @classmethod
    def order_company(cls, tables):
        table, _ = tables[None]
        return chain.from_iterable(
            [cls.company.convert_order('company.party.name', tables, cls), [Column(table, 'identification')]]
        )

    @classmethod
    def validate(cls, mandates):
        super(Mandate, cls).validate(mandates)
        for mandate in mandates:
            mandate.validate_active()
            mandate.check_xml_characters()

    def validate_active(self):
        # Deactivate mandate as unit mandate on canceled state
        if (self.id > 0) and self.state == 'canceled':
            condoparties = Pool().get('condo.party').__table__()
            condopayments = Pool().get('condo.payment').__table__()

            with Transaction().connection.cursor() as cursor:
                cursor.execute(
                    *condopayments.select(
                        condopayments.id,
                        where=(condopayments.mandate == self.id)
                        & ((condopayments.state == 'draft') | (condopayments.state == 'approved')),
                    )
                )
                ids = [ids for (ids,) in cursor.fetchall()]
                if len(ids):
                    self.raise_user_error(
                        'Can\'t cancel mandate "%s".\nThere are %s payments in draft or approved state with this mandate!',
                        (self.identification, len(ids)),
                    )

                cursor.execute(*condoparties.select(condoparties.id, where=(condoparties.mandate == self.id)))
                ids = [ids for (ids,) in cursor.fetchall()]
                if len(ids):
                    self.raise_user_warning(
                        'warn_canceled_mandate',
                        'Mandate "%s" will be canceled as mean of payment in %d unit(s)/apartment(s)!',
                        (self.identification, len(ids)),
                    )

                    for sub_ids in grouped_slice(ids):
                        red_sql = reduce_ids(condoparties.id, sub_ids)
                        # Use SQL to prevent double validate loop
                        cursor.execute(
                            *condoparties.update(columns=[condoparties.mandate], values=[None], where=red_sql)
                        )

    def check_xml_characters(self):
        if self.identification and '//' in self.identification:
            self.raise_user_error("Mandate Identification can't contain 2 consecutive '/'")
        if self.identification and (self.identification.startswith('/') or self.identification.endswith('/')):
            self.raise_user_error("Mandate Identification can't start or end with '/' character")

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
        super(Mandate, cls).write(*args)

    @classmethod
    def copy(cls, mandates, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default.setdefault('state', 'draft')
        default.setdefault('payments', [])
        default.setdefault('signature_date', None)
        default.setdefault('identification', None)
        return super(Mandate, cls).copy(mandates, default=default)

    @classmethod
    def get_has_payments(cls, mandates, name):
        pool = Pool()
        Payment = pool.get('condo.payment')
        payment = Payment.__table__()
        cursor = Transaction().connection.cursor()

        has_payments = dict.fromkeys([m.id for m in mandates], False)

        for sub_ids in grouped_slice(mandates):
            red_sql = reduce_ids(payment.mandate, sub_ids)
            cursor.execute(*payment.select(payment.mandate, Literal(True), where=red_sql, group_by=payment.mandate))
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
            if (mandate.state == 'draft') or (mandate.state == 'canceled' and not mandate.has_payments):
                continue
            cls.raise_user_error('delete_draft_canceled', mandate.rec_name)
        super(Mandate, cls).delete(mandates)


class MandateReport(CompanyReport):
    __name__ = 'account.payment.sepa.mandate'


class CheckMandatesList(ModelView):
    'Check Mandates List'
    __name__ = 'condo.check_mandates.result'
    mandates = fields.Many2Many('condo.payment.sepa.mandate', None, None, 'Mandates not used', readonly=True)
    units = fields.Many2Many('condo.unit', None, None, 'Units without mandates', readonly=True)


class CheckMandates(Wizard):
    'Check Mandates List'
    __name__ = 'condo.check_mandates'
    start_state = 'check'

    check = StateTransition()
    result = StateView(
        'condo.check_mandates.result',
        'condominium_payment_sepa.check_mandates_result',
        [Button('OK', 'end', 'tryton-ok', True)],
    )

    def transition_check(self):

        pool = Pool()
        Unit = pool.get('condo.unit')
        Mandate = pool.get('condo.payment.sepa.mandate')

        mandates = Mandate.search_read(
            [
                [
                    'OR',
                    ('company', 'in', Transaction().context.get('active_ids')),
                    ('company.parent', 'child_of', Transaction().context.get('active_ids')),
                ],
                ('state', 'not in', ('canceled',)),
                ('condoparties', '=', None),
            ],
            fields_names=['id'],
        )

        units = Unit.search_read(
            [
                'OR',
                ('company', 'in', Transaction().context.get('active_ids')),
                ('company.parent', 'child_of', Transaction().context.get('active_ids')),
            ],
            fields_names=['id'],
        )

        units_with_mandate = Unit.search_read(
            [
                [
                    'OR',
                    ('company', 'in', Transaction().context.get('active_ids')),
                    ('company.parent', 'child_of', Transaction().context.get('active_ids')),
                ],
                ('condoparties.mandate', '!=', None),
            ],
            fields_names=['id'],
        )

        self.result.mandates = [r['id'] for r in mandates]
        self.result.units = [r['id'] for r in units if r not in units_with_mandate]
        return 'result'

    def default_result(self, fields):
        return {'mandates': [p.id for p in self.result.mandates], 'units': [p.id for p in self.result.units]}
