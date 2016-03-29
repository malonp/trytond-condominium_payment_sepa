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


from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval, If, Bool

__all__ = ['CondoParty']
__metaclass__ = PoolMeta


class CondoParty:
    'Condominium Party'
    __name__ = 'condo.party'
    sepa_mandate = fields.Many2One('condo.payment.sepa.mandate', 'Mandate',
        help="SEPA Mandate of this party for the unit",
        depends=['isactive', 'company'],
        domain=[If(Bool(Eval('company')),
                     [
                         ('company', '=', Eval('company')),
                     ],[]),
                ('state', 'not in', ['canceled'])],
        ondelete='SET NULL', states={
            'readonly': ~Eval('isactive')
            })
