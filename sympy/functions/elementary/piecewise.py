from __future__ import print_function, division

from sympy.core import Basic, S, Function, diff, Tuple, Symbol
from sympy.core.basic import as_Basic
from sympy.core.relational import Equality, Relational, _canonical
from sympy.core.sympify import _sympify, SympifyError
from sympy.functions.elementary.miscellaneous import Max, Min
from sympy.logic.boolalg import (And, Boolean, distribute_and_over_or,
    true, false, Not, Or, ITE, simplify_logic)
from sympy.core.compatibility import default_sort_key, range
from sympy.utilities.misc import func_name, filldedent


class ExprCondPair(Tuple):
    """Represents an expression, condition pair."""

    def __new__(cls, expr, cond):
        expr = as_Basic(expr)
        if cond == True:
            return Tuple.__new__(cls, expr, true)
        elif cond == False:
            return Tuple.__new__(cls, expr, false)
        elif isinstance(cond, Basic) and cond.has(Piecewise):
            cond = piecewise_fold(cond)
            if isinstance(cond, Piecewise):
                cond = cond.rewrite(ITE)

        if not isinstance(cond, Boolean):
            raise TypeError(filldedent('''
                Second argument must be a Boolean,
                not `%s`''' % func_name(cond)))
        return Tuple.__new__(cls, expr, cond)

    @property
    def expr(self):
        """
        Returns the expression of this pair.
        """
        return self.args[0]

    @property
    def cond(self):
        """
        Returns the condition of this pair.
        """
        return self.args[1]

    @property
    def free_symbols(self):
        """
        Return the free symbols of this pair.
        """
        # Overload Basic.free_symbols because self.args[1] may contain non-Basic
        result = self.expr.free_symbols
        if hasattr(self.cond, 'free_symbols'):
            result |= self.cond.free_symbols
        return result

    @property
    def is_commutative(self):
        return self.expr.is_commutative

    def __iter__(self):
        yield self.expr
        yield self.cond


class Piecewise(Function):
    """
    Represents a piecewise function.

    Usage:

      Piecewise( (expr,cond), (expr,cond), ... )
        - Each argument is a 2-tuple defining an expression and condition
        - The conds are evaluated in turn returning the first that is True.
          If any of the evaluated conds are not determined explicitly False,
          e.g. x < 1, the function is returned in symbolic form.
        - If the function is evaluated at a place where all conditions are False,
          a ValueError exception will be raised.
        - Pairs where the cond is explicitly False, will be removed.

    Examples
    ========

    >>> from sympy import Piecewise, log, ITE, piecewise_fold
    >>> from sympy.abc import x, y
    >>> f = x**2
    >>> g = log(x)
    >>> p = Piecewise((0, x < -1), (f, x <= 1), (g, True))
    >>> p.subs(x,1)
    1
    >>> p.subs(x,5)
    log(5)

    Booleans can contain Piecewise elements:

    >>> cond = (x < y).subs(x, Piecewise((2, x < 0), (3, True))); cond
    Piecewise((2, x < 0), (3, True)) < y

    The folded version of this results in a Piecewise whose
    expressions are Booleans:

    >>> folded_cond = piecewise_fold(cond); folded_cond
    Piecewise((2 < y, x < 0), (3 < y, True))

    When a Boolean containing Piecewise (like cond) or a Piecewise
    with Boolean expressions (like folded_cond) is used as a condition,
    it is converted to an equivalent ITE object:

    >>> Piecewise((1, folded_cond))
    Piecewise((1, ITE(x < 0, y > 2, y > 3)))

    When a condition is an ITE, it will be converted to a simplified
    Boolean expression:

    >>> piecewise_fold(_)
    Piecewise((1, ((x >= 0) | (y > 2)) & ((y > 3) | (x < 0))))

    See Also
    ========
    piecewise_fold, ITE
    """

    nargs = None
    is_Piecewise = True

    def __new__(cls, *args, **options):
        # (Try to) sympify args first
        newargs = []
        for ec in args:
            # ec could be a ExprCondPair or a tuple
            pair = ExprCondPair(*getattr(ec, 'args', ec))
            cond = pair.cond
            if cond is false:
                continue
            newargs.append(pair)
            if cond is true:
                break

        if options.pop('evaluate', True):
            r = cls.eval(*newargs)
        else:
            r = None

        if r is None:
            return Basic.__new__(cls, *newargs, **options)
        else:
            return r

    @classmethod
    def eval(cls, *args):
        # Check for situations where we can evaluate the Piecewise object.
        # 1) Hit an unevaluable cond (e.g. x<1) -> keep object
        # 2) Hit a true condition -> return that expr
        # 3) Remove false conditions, if no conditions left -> raise ValueError
        all_conds_evaled = True    # Do all conds eval to a bool?
        piecewise_again = False    # Should we pass args to Piecewise again?
        non_false_ecpairs = []
        or1 = Or(*[cond for (_, cond) in args if cond != true])
        for expr, cond in args:
            # Check here if expr is a Piecewise and collapse if one of
            # the conds in expr matches cond. This allows the collapsing
            # of Piecewise((Piecewise(x,x<0),x<0)) to Piecewise((x,x<0)).
            # This is important when using piecewise_fold to simplify
            # multiple Piecewise instances having the same conds.
            # Eventually, this code should be able to collapse Piecewise's
            # having different intervals, but this will probably require
            # using the new assumptions.
            if isinstance(expr, Piecewise):
                or2 = Or(*[c for (_, c) in expr.args if c != true])
                for e, c in expr.args:
                    # Don't collapse if cond is "True" as this leads to
                    # incorrect simplifications with nested Piecewises.
                    if c == cond and (or1 == or2 or cond != true):
                        expr = e
                        piecewise_again = True
            cond_eval = cls.__eval_cond(cond)
            if cond_eval is None:
                all_conds_evaled = False
            elif cond_eval:
                if all_conds_evaled:
                    return expr
            if len(non_false_ecpairs) != 0:
                if non_false_ecpairs[-1].cond == cond:
                    continue
                elif non_false_ecpairs[-1].expr == expr:
                    newcond = Or(cond, non_false_ecpairs[-1].cond)
                    if isinstance(newcond, (And, Or)):
                        newcond = distribute_and_over_or(newcond)
                    non_false_ecpairs[-1] = ExprCondPair(expr, newcond)
                    continue
            non_false_ecpairs.append(ExprCondPair(expr, cond))
        if len(non_false_ecpairs) != len(args) or piecewise_again:
            return cls(*non_false_ecpairs)

        return None

    def doit(self, **hints):
        """
        Evaluate this piecewise function.
        """
        newargs = []
        for e, c in self.args:
            if hints.get('deep', True):
                if isinstance(e, Basic):
                    e = e.doit(**hints)
                if isinstance(c, Basic):
                    c = c.doit(**hints)
            newargs.append((e, c))
        return self.func(*newargs)

    def _eval_as_leading_term(self, x):
        for e, c in self.args:
            if c == True or c.subs(x, 0) == True:
                return e.as_leading_term(x)

    def _eval_adjoint(self):
        return self.func(*[(e.adjoint(), c) for e, c in self.args])

    def _eval_conjugate(self):
        return self.func(*[(e.conjugate(), c) for e, c in self.args])

    def _eval_derivative(self, x):
        return self.func(*[(diff(e, x), c) for e, c in self.args])

    def _eval_evalf(self, prec):
        return self.func(*[(e.evalf(prec), c) for e, c in self.args])

    def _eval_integral(self, x):
        from sympy.integrals import integrate
        return self.func(*[(integrate(e, x), c) for e, c in self.args])

    def _eval_interval(self, sym, a, b):
        """Evaluates the function along the sym in a given interval ab"""
        # FIXME: Currently complex intervals are not supported.  A possible
        # replacement algorithm, discussed in issue 5227, can be found in the
        # following papers;
        #     http://portal.acm.org/citation.cfm?id=281649
        #     http://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.70.4127&rep=rep1&type=pdf

        if a is None or b is None:
            # In this case, it is just simple substitution
            return piecewise_fold(
                super(Piecewise, self)._eval_interval(sym, a, b))

        mul = 1
        if (a == b) == True:
            return S.Zero
        elif (a > b) == True:
            a, b, mul = b, a, -1
        elif (a <= b) != True:
            newargs = []
            for e, c in self.args:
                intervals = self._sort_expr_cond(
                    sym, S.NegativeInfinity, S.Infinity, c)
                values = []
                for lower, upper, expr in intervals:
                    if (a < lower) == True:
                        mid = lower
                        rep = b
                        val = e._eval_interval(sym, mid, b)
                        val += self._eval_interval(sym, a, mid)
                    elif (a > upper) == True:
                        mid = upper
                        rep = b
                        val = e._eval_interval(sym, mid, b)
                        val += self._eval_interval(sym, a, mid)
                    elif (a >= lower) == True and (a <= upper) == True:
                        rep = b
                        val = e._eval_interval(sym, a, b)
                    elif (b < lower) == True:
                        mid = lower
                        rep = a
                        val = e._eval_interval(sym, a, mid)
                        val += self._eval_interval(sym, mid, b)
                    elif (b > upper) == True:
                        mid = upper
                        rep = a
                        val = e._eval_interval(sym, a, mid)
                        val += self._eval_interval(sym, mid, b)
                    elif ((b >= lower) == True) and ((b <= upper) == True):
                        rep = a
                        val = e._eval_interval(sym, a, b)
                    else:
                        raise NotImplementedError(
                            """The evaluation of a Piecewise interval when both the lower
                            and the upper limit are symbolic is not yet implemented.""")
                    values.append(val)
                if len(set(values)) == 1:
                    try:
                        c = c.subs(sym, rep)
                    except AttributeError:
                        pass
                    e = values[0]
                    newargs.append((e, c))
                else:
                    for i in range(len(values)):
                        newargs.append((values[i], (c == True and i == len(values) - 1) or
                            And(rep >= intervals[i][0], rep <= intervals[i][1])))
            return self.func(*newargs)

        # Determine what intervals the expr,cond pairs affect.
        int_expr = self._sort_expr_cond(sym, a, b)

        # Finally run through the intervals and sum the evaluation.
        ret_fun = 0
        for int_a, int_b, expr in int_expr:
            if isinstance(expr, Piecewise):
                # If we still have a Piecewise by now, _sort_expr_cond would
                # already have determined that its conditions are independent
                # of the integration variable, thus we just use substitution.
                ret_fun += piecewise_fold(
                    super(Piecewise, expr)._eval_interval(sym, Max(a, int_a), Min(b, int_b)))
            else:
                ret_fun += expr._eval_interval(sym, Max(a, int_a), Min(b, int_b))
        return mul * ret_fun

    def _sort_expr_cond(self, sym, a, b, targetcond=None):
        """Determine what intervals the expr, cond pairs affect.

        1) If cond is True, then log it as default
        1.1) Currently if cond can't be evaluated, throw NotImplementedError.
        2) For each inequality, if previous cond defines part of the interval
           update the new conds interval.
           -  eg x < 1, x < 3 -> [oo,1],[1,3] instead of [oo,1],[oo,3]
        3) Sort the intervals to make it easier to find correct exprs

        Under normal use, we return the expr,cond pairs in increasing order
        along the real axis corresponding to the symbol sym.  If targetcond
        is given, we return a list of (lowerbound, upperbound) pairs for
        this condition."""
        from sympy.solvers.inequalities import _solve_inequality
        default = None
        int_expr = []
        expr_cond = []
        or_cond = False
        or_intervals = []
        independent_expr_cond = []
        for expr, cond in self.args:
            if isinstance(cond, Or):
                for cond2 in sorted(cond.args, key=default_sort_key):
                    expr_cond.append((expr, cond2))
            else:
                expr_cond.append((expr, cond))
            if cond == True:
                break
        for expr, cond in expr_cond:
            if cond == True:
                independent_expr_cond.append((expr, cond))
                default = self.func(*independent_expr_cond)
                break
            orig_cond = cond
            if sym not in cond.free_symbols:
                independent_expr_cond.append((expr, cond))
                continue
            elif isinstance(cond, Equality):
                continue
            elif isinstance(cond, And):
                lower = S.NegativeInfinity
                upper = S.Infinity
                for cond2 in cond.args:
                    if sym not in [cond2.lts, cond2.gts]:
                        cond2 = _solve_inequality(cond2, sym)
                    if cond2.lts == sym:
                        upper = Min(cond2.gts, upper)
                    elif cond2.gts == sym:
                        lower = Max(cond2.lts, lower)
                    else:
                        raise NotImplementedError(
                            "Unable to handle interval evaluation of expression.")
            else:
                if sym not in [cond.lts, cond.gts]:
                    cond = _solve_inequality(cond, sym)
                lower, upper = cond.lts, cond.gts  # part 1: initialize with givens
                if cond.lts == sym:                # part 1a: expand the side ...
                    lower = S.NegativeInfinity   # e.g. x <= 0 ---> -oo <= 0
                elif cond.gts == sym:            # part 1a: ... that can be expanded
                    upper = S.Infinity           # e.g. x >= 0 --->  oo >= 0
                else:
                    raise NotImplementedError(
                        "Unable to handle interval evaluation of expression.")

            # part 1b: Reduce (-)infinity to what was passed in.
            lower, upper = Max(a, lower), Min(b, upper)

            for n in range(len(int_expr)):
                # Part 2: remove any interval overlap.  For any conflicts, the
                # iterval already there wins, and the incoming interval updates
                # its bounds accordingly.
                if self.__eval_cond(lower < int_expr[n][1]) and \
                        self.__eval_cond(lower >= int_expr[n][0]):
                    lower = int_expr[n][1]
                elif len(int_expr[n][1].free_symbols) and \
                        self.__eval_cond(lower >= int_expr[n][0]):
                    if self.__eval_cond(lower == int_expr[n][0]):
                        lower = int_expr[n][1]
                    else:
                        int_expr[n][1] = Min(lower, int_expr[n][1])
                elif len(int_expr[n][0].free_symbols) and \
                        self.__eval_cond(upper == int_expr[n][1]):
                    upper = Min(upper, int_expr[n][0])
                elif len(int_expr[n][1].free_symbols) and \
                        (lower >= int_expr[n][0]) != True and \
                        (int_expr[n][1] == Min(lower, upper)) != True:
                    upper = Min(upper, int_expr[n][0])
                elif self.__eval_cond(upper > int_expr[n][0]) and \
                        self.__eval_cond(upper <= int_expr[n][1]):
                    upper = int_expr[n][0]
                elif len(int_expr[n][0].free_symbols) and \
                        self.__eval_cond(upper < int_expr[n][1]):
                    int_expr[n][0] = Max(upper, int_expr[n][0])

            if self.__eval_cond(lower >= upper) != True:  # Is it still an interval?
                int_expr.append([lower, upper, expr])
            if orig_cond == targetcond:
                return [(lower, upper, None)]
            elif isinstance(targetcond, Or) and cond in targetcond.args:
                or_cond = Or(or_cond, cond)
                or_intervals.append((lower, upper, None))
                if or_cond == targetcond:
                    or_intervals.sort(key=lambda x: x[0])
                    return or_intervals

        int_expr.sort(key=lambda x: x[1].sort_key(
        ) if x[1].is_number else S.NegativeInfinity.sort_key())
        int_expr.sort(key=lambda x: x[0].sort_key(
        ) if x[0].is_number else S.Infinity.sort_key())

        for n in range(len(int_expr)):
            if len(int_expr[n][0].free_symbols) or len(int_expr[n][1].free_symbols):
                if isinstance(int_expr[n][1], Min) or int_expr[n][1] == b:
                    newval = Min(*int_expr[n][:-1])
                    if n > 0 and int_expr[n][0] == int_expr[n - 1][1]:
                        int_expr[n - 1][1] = newval
                    int_expr[n][0] = newval
                else:
                    newval = Max(*int_expr[n][:-1])
                    if n < len(int_expr) - 1 and int_expr[n][1] == int_expr[n + 1][0]:
                        int_expr[n + 1][0] = newval
                    int_expr[n][1] = newval

        # Add holes to list of intervals if there is a default value,
        # otherwise raise a ValueError.
        holes = []
        curr_low = a
        for int_a, int_b, expr in int_expr:
            if (curr_low < int_a) == True:
                holes.append([curr_low, Min(b, int_a), default])
            elif (curr_low >= int_a) != True:
                holes.append([curr_low, Min(b, int_a), default])
            curr_low = Min(b, int_b)
        if (curr_low < b) == True:
            holes.append([Min(b, curr_low), b, default])
        elif (curr_low >= b) != True:
            holes.append([Min(b, curr_low), b, default])

        if holes and default is not None:
            int_expr.extend(holes)
            if targetcond == True:
                return [(h[0], h[1], None) for h in holes]
        elif holes and default is None:
            raise ValueError("Called interval evaluation over piecewise "
                             "function on undefined intervals %s" %
                             ", ".join([str((h[0], h[1])) for h in holes]))

        return int_expr

    def _eval_nseries(self, x, n, logx):
        args = [(ec.expr._eval_nseries(x, n, logx), ec.cond) for ec in self.args]
        return self.func(*args)

    def _eval_power(self, s):
        return self.func(*[(e**s, c) for e, c in self.args])

    def _eval_subs(self, old, new):
        # this is strictly not necessary, but we can keep track
        # of whether True or False conditions arise and be
        # somewhat more efficient by avoiding other substitutions
        # and avoiding invalid conditions that appear after a
        # True condition
        args = list(self.args)
        for i, (e, c) in enumerate(args):
            c = c._subs(old, new)
            if c != False:
                e = e._subs(old, new)
            args[i] = (e, c)
            if c == True:
                break
        return self.func(*args)

    def _eval_transpose(self):
        return self.func(*[(e.transpose(), c) for e, c in self.args])

    def _eval_template_is_attr(self, is_attr):
        b = None
        for expr, _ in self.args:
            a = getattr(expr, is_attr)
            if a is None:
                return
            if b is None:
                b = a
            elif b is not a:
                return
        return b

    _eval_is_finite = lambda self: self._eval_template_is_attr(
        'is_finite')
    _eval_is_complex = lambda self: self._eval_template_is_attr('is_complex')
    _eval_is_even = lambda self: self._eval_template_is_attr('is_even')
    _eval_is_imaginary = lambda self: self._eval_template_is_attr(
        'is_imaginary')
    _eval_is_integer = lambda self: self._eval_template_is_attr('is_integer')
    _eval_is_irrational = lambda self: self._eval_template_is_attr(
        'is_irrational')
    _eval_is_negative = lambda self: self._eval_template_is_attr('is_negative')
    _eval_is_nonnegative = lambda self: self._eval_template_is_attr(
        'is_nonnegative')
    _eval_is_nonpositive = lambda self: self._eval_template_is_attr(
        'is_nonpositive')
    _eval_is_nonzero = lambda self: self._eval_template_is_attr(
        'is_nonzero')
    _eval_is_odd = lambda self: self._eval_template_is_attr('is_odd')
    _eval_is_polar = lambda self: self._eval_template_is_attr('is_polar')
    _eval_is_positive = lambda self: self._eval_template_is_attr('is_positive')
    _eval_is_real = lambda self: self._eval_template_is_attr('is_real')
    _eval_is_zero = lambda self: self._eval_template_is_attr(
        'is_zero')

    @classmethod
    def __eval_cond(cls, cond):
        """Return the truth value of the condition."""
        from sympy.solvers.solvers import checksol
        if cond == True:
            return True
        if isinstance(cond, Equality):
            try:
                diff = cond.lhs - cond.rhs
                if diff.is_commutative:
                    return diff.is_zero
            except TypeError:
                pass

    def as_expr_set_pairs(self):
        """Return tuples for each argument of self that give
        the expression and the interval in which it is valid.
        If a condition cannot be converted to a set, an error
        will be raised. The variable of the conditions is
        assumed to be real; sets of real values are returned.

        Examples
        ========
        >>> from sympy import Piecewise
        >>> from sympy.abc import x
        >>> Piecewise(
        ...     (1, x < 2),
        ...     (2,(x > 0) & (x < 4)),
        ...     (3, True)).as_expr_set_pairs()
        [(1, Interval.open(-oo, 2)),
         (2, Interval.Ropen(2, 4)),
         (3, Interval(4, oo))]
        """
        exp_sets = []
        U = S.Reals
        for expr, cond in self.args:
            cond_int = U.intersect(cond.as_set())
            U = U - cond_int
            exp_sets.append((expr, cond_int))
        return exp_sets

    def _eval_rewrite_as_ITE(self, *args):
        byfree = {}
        args = list(args)
        default = any(c == True for b, c in args)
        for i, (b, c) in enumerate(args):
            if not isinstance(b, Boolean) and b != True:
                raise TypeError(filldedent('''
                    Expecting Boolean or bool but got `%s`
                    ''' % func_name(b)))
            if c == True:
                break
            # loop over independent conditions for this b
            for c in c.args if isinstance(c, Or) else [c]:
                free = c.free_symbols
                x = free.pop()
                try:
                    byfree[x] = byfree.setdefault(
                        x, S.EmptySet).union(c.as_set())
                except NotImplementedError:
                    if not default:
                        raise NotImplementedError(filldedent('''
                            A method to determine whether a multivariate
                            conditional is consistent with a complete coverage
                            of all variables has not been implemented so the
                            rewrite is being stopped after encountering `%s`.
                            This error would not occur if a default expression
                            like `(foo, True)` were given.
                            ''' % c))
                if byfree[x] in (S.UniversalSet, S.Reals):
                    # collapse the ith condition to True and break
                    args[i] = list(args[i])
                    c = args[i][1] = True
                    break
            if c == True:
                break
        if c != True:
            raise ValueError(filldedent('''
                Conditions must cover all reals or a final default
                condition `(foo, True)` must be given.
                '''))
        last, _ = args[i]  # ignore all past ith arg
        for a, c in reversed(args[:i]):
            last = ITE(c, a, last)
        return _canonical(last)


def piecewise_fold(expr):
    """
    Takes an expression containing a piecewise function and returns the
    expression in piecewise form. In addition, any ITE conditions are
    rewritten in negation normal form and simplified.

    Examples
    ========

    >>> from sympy import Piecewise, piecewise_fold, sympify as S
    >>> from sympy.abc import x
    >>> p = Piecewise((x, x < 1), (1, S(1) <= x))
    >>> piecewise_fold(x*p)
    Piecewise((x**2, x < 1), (x, 1 <= x))

    See Also
    ========

    Piecewise
    """
    if not isinstance(expr, Basic) or not expr.has(Piecewise):
        return expr

    new_args = []
    if isinstance(expr, (ExprCondPair, Piecewise)):
        for e, c in expr.args:
            if not isinstance(e, Piecewise):
                e = piecewise_fold(e)
            # we don't keep Piecewise in condition because
            # it has to be checked to see that it's complete
            # and we convert it to ITE at that time
            assert not c.has(Piecewise)  # pragma: no cover
            if isinstance(c, ITE):
                c = c.to_nnf()
                c = simplify_logic(c, form='cnf')
            if isinstance(e, Piecewise):
                new_args.extend([(piecewise_fold(ei), And(ci, c))
                    for ei, ci in e.args])
            else:
                new_args.append((e, c))
    else:
        from sympy.utilities.iterables import cartes
        folded = list(map(piecewise_fold, expr.args))
        for ec in cartes(*[
                (i.args if isinstance(i, Piecewise) else
                 [(i, true)]) for i in folded]):
            e, c = zip(*ec)
            new_args.append((expr.func(*e), And(*c)))

    return Piecewise(*new_args)
