import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n";
import { useAuth } from "@/features/auth/AuthProvider";
import { api } from "@/lib/api";
import {
  useBankAccounts,
  useBankTransaction,
  useBankTransactions,
  useMatchCandidates,
} from "@/lib/queries";
import { describeError, errorRequestId } from "@/lib/errorMessage";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Checkbox,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Select,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type {
  BankTransaction,
  BankTransactionDetail,
  MatchableKind,
  TransactionStatus,
} from "@/lib/types";

function StatusBadge({ status }: { status: TransactionStatus }) {
  const { t } = useI18n();
  if (status === "MATCHED")
    return <Badge tone="success">{t.banking.matched}</Badge>;
  if (status === "IGNORED") return <Badge>{t.banking.ignored}</Badge>;
  return <Badge tone="warning">{t.banking.unmatched}</Badge>;
}

export function ReconcilePanel() {
  const { t, formatDate } = useI18n();
  const queryClient = useQueryClient();

  const accounts = useBankAccounts(true);
  const [accountId, setAccountId] = useState("");
  const [status, setStatus] = useState<TransactionStatus | "">("UNMATCHED");
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const transactions = useBankTransactions(true, {
    bank_account_id: accountId || undefined,
    transaction_status: status || undefined,
  });

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ["banking"] });

  if (transactions.isLoading || accounts.isLoading) {
    return <LoadingState label={t.common.loading} />;
  }
  if (transactions.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(transactions.error, t)}
        requestId={errorRequestId(transactions.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void transactions.refetch()}
        retryLabel={t.common.retry}
      />
    );
  }

  const accountRows = accounts.data ?? [];
  const rows = transactions.data ?? [];

  return (
    <>
      {notice ? (
        <div className="mb-4">
          <Alert tone="success">{notice}</Alert>
        </div>
      ) : null}
      {error ? (
        <div className="mb-4">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_28rem]">
        <Card>
          <CardHeader
            title={t.banking.queue}
            actions={
              <>
                <Select
                  aria-label={t.banking.account}
                  value={accountId}
                  onChange={(event) => setAccountId(event.target.value)}
                >
                  <option value="">{t.banking.allAccounts}</option>
                  {accountRows.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.code}
                    </option>
                  ))}
                </Select>
                <Select
                  aria-label={t.banking.status}
                  value={status}
                  onChange={(event) =>
                    setStatus(event.target.value as TransactionStatus | "")
                  }
                >
                  <option value="UNMATCHED">{t.banking.unmatched}</option>
                  <option value="MATCHED">{t.banking.matched}</option>
                  <option value="IGNORED">{t.banking.ignored}</option>
                  <option value="">{t.banking.allStatuses}</option>
                </Select>
              </>
            }
          />

          {accountRows.length === 0 ? (
            <EmptyState
              title={t.banking.emptyAccounts}
              body={t.banking.addFirstAccount}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              title={t.banking.emptyQueue}
              body={t.banking.importFirst}
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>{t.banking.valueDate}</Th>
                  <Th>{t.banking.description}</Th>
                  <Th>{t.banking.direction}</Th>
                  <Th numeric>{t.banking.amount}</Th>
                  <Th>{t.banking.status}</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((line: BankTransaction) => (
                  <tr
                    key={line.id}
                    onClick={() => setSelected(line.id)}
                    className={
                      "cursor-pointer " +
                      (line.id === selected
                        ? "bg-brand-50"
                        : "hover:bg-[var(--surface-sunken)]")
                    }
                  >
                    <Td>{formatDate(line.value_date)}</Td>
                    <Td>
                      {line.description ?? "—"}
                      {line.counterparty_name ? (
                        <div className="text-[var(--text-secondary)]">
                          {line.counterparty_name}
                        </div>
                      ) : null}
                    </Td>
                    <Td>
                      {line.direction === "IN"
                        ? t.banking.inflow
                        : t.banking.outflow}
                    </Td>
                    <Td numeric>{line.amount}</Td>
                    <Td>
                      <StatusBadge status={line.status} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        {selected ? (
          <TransactionDetail
            transactionId={selected}
            onChanged={(message) => {
              setError(null);
              setNotice(message);
              invalidate();
            }}
            onError={(message) => {
              setNotice(null);
              setError(message);
            }}
          />
        ) : (
          <Card>
            <EmptyState
              title={t.banking.selectLine}
              body={t.banking.matchHint}
            />
          </Card>
        )}
      </div>
    </>
  );
}

function TransactionDetail({
  transactionId,
  onChanged,
  onError,
}: {
  transactionId: string;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}) {
  const { t, formatDate } = useI18n();
  const { can } = useAuth();
  const line = useBankTransaction(transactionId);
  const [matchOpen, setMatchOpen] = useState(false);
  const [ignoreOpen, setIgnoreOpen] = useState(false);

  const mayReconcile = can("banking.reconcile");

  const reopen = useMutation({
    mutationFn: () =>
      api.post<BankTransaction>(
        `/banking/transactions/${transactionId}/reopen`,
      ),
    onSuccess: () => onChanged(t.banking.reopened),
    onError: (err) => onError(describeError(err, t)),
  });

  if (line.isLoading) return <LoadingState label={t.common.loading} />;
  if (!line.data) return null;

  const data = line.data;
  const kindLabel: Record<MatchableKind, string> = {
    CASH_DEPOSIT: t.banking.kindCashDeposit,
    CARD_PAYMENT: t.banking.kindCardPayment,
    BANK_PAYMENT: t.banking.kindBankPayment,
  };

  return (
    <>
      <Card>
        <CardHeader
          title={`${formatDate(data.value_date)} · ${data.amount}`}
          description={data.description ?? undefined}
          actions={
            mayReconcile ? (
              <>
                {data.status !== "MATCHED" && data.status !== "IGNORED" ? (
                  <>
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => setMatchOpen(true)}
                    >
                      {t.banking.match}
                    </Button>
                    <Button size="sm" onClick={() => setIgnoreOpen(true)}>
                      {t.banking.setAside}
                    </Button>
                  </>
                ) : null}
                {data.status === "IGNORED" ? (
                  <Button
                    size="sm"
                    loading={reopen.isPending}
                    onClick={() => reopen.mutate()}
                  >
                    {t.banking.reopen}
                  </Button>
                ) : null}
              </>
            ) : undefined
          }
        />

        <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-[var(--text-secondary)]">{t.banking.status}</dt>
          <dd className="text-right">
            <StatusBadge status={data.status} />
          </dd>
          <dt className="text-[var(--text-secondary)]">
            {t.banking.direction}
          </dt>
          <dd className="text-right">
            {data.direction === "IN" ? t.banking.inflow : t.banking.outflow}
          </dd>
          <dt className="text-[var(--text-secondary)]">
            {t.banking.documentNumber}
          </dt>
          <dd className="text-right">{data.document_number ?? "—"}</dd>
          <dt className="text-[var(--text-secondary)]">
            {t.banking.counterparty}
          </dt>
          <dd className="text-right">{data.counterparty_name ?? "—"}</dd>
          <dt className="text-[var(--text-secondary)]">
            {t.banking.matchedAmount}
          </dt>
          <dd className="tabular text-right">{data.matched_amount}</dd>
          <dt className="text-[var(--text-secondary)]">
            {t.banking.commission}
          </dt>
          <dd className="tabular text-right">{data.commission_amount}</dd>
          <dt className="font-medium">{t.banking.unaccounted}</dt>
          <dd className="tabular text-right font-medium">
            {data.unaccounted_amount}
          </dd>
        </dl>

        {data.ignored_reason ? (
          <div className="mb-4">
            <Alert tone="info">
              {t.banking.ignored}: {data.ignored_reason}
            </Alert>
          </div>
        ) : null}

        {data.matches.length === 0 ? (
          <EmptyState
            title={t.banking.matchesOnThisLine}
            body={t.banking.matchHint}
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.banking.matchesOnThisLine}</Th>
                <Th numeric>{t.banking.amount}</Th>
              </tr>
            </thead>
            <tbody>
              {data.matches.map((match) => (
                <tr key={match.id}>
                  <Td>
                    {kindLabel[match.source_kind]}
                    {match.note ? (
                      <div className="text-[var(--text-secondary)]">
                        {match.note}
                      </div>
                    ) : null}
                  </Td>
                  <Td numeric>{match.amount}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <MatchDialog
        open={matchOpen}
        line={data}
        onClose={() => setMatchOpen(false)}
        onDone={() => {
          setMatchOpen(false);
          onChanged(t.banking.matchRecorded);
        }}
        onError={(message) => {
          setMatchOpen(false);
          onError(message);
        }}
      />

      <IgnoreDialog
        open={ignoreOpen}
        transactionId={transactionId}
        onClose={() => setIgnoreOpen(false)}
        onDone={() => {
          setIgnoreOpen(false);
          onChanged(t.banking.setAsideDone);
        }}
        onError={(message) => {
          setIgnoreOpen(false);
          onError(message);
        }}
      />
    </>
  );
}

function MatchDialog({
  open,
  line,
  onClose,
  onDone,
  onError,
}: {
  open: boolean;
  line: BankTransactionDetail;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const { t, formatDate } = useI18n();
  const candidates = useMatchCandidates(open ? line.id : null);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [commission, setCommission] = useState("");
  const [note, setNote] = useState("");

  // Reopening the dialog on a different line must not carry the previous
  // line's selection into it.
  useEffect(() => {
    if (!open) {
      setChosen({});
      setCommission("");
      setNote("");
    }
  }, [open]);

  const rows = candidates.data ?? [];
  const kindLabel: Record<MatchableKind, string> = {
    CASH_DEPOSIT: t.banking.kindCashDeposit,
    CARD_PAYMENT: t.banking.kindCardPayment,
    BANK_PAYMENT: t.banking.kindBankPayment,
  };

  /** What the chosen records account for, once the fee is taken off. */
  const accounted = useMemo(() => {
    const gross = Object.values(chosen).reduce(
      (total, amount) => total + (Number(amount) || 0),
      0,
    );
    return gross - (Number(commission) || 0);
  }, [chosen, commission]);

  const unaccounted = Number(line.unaccounted_amount);
  const onlyCardsChosen = rows
    .filter((candidate) => candidate.id in chosen)
    .every((candidate) => candidate.kind === "CARD_PAYMENT");

  const act = useMutation({
    mutationFn: () =>
      api.post<BankTransactionDetail>(
        `/banking/transactions/${line.id}/match`,
        {
          records: rows
            .filter((candidate) => candidate.id in chosen)
            .map((candidate) => ({
              kind: candidate.kind,
              id: candidate.id,
              amount: chosen[candidate.id],
            })),
          commission: commission || "0",
          note: note || null,
        },
      ),
    onSuccess: onDone,
    onError: (err) => onError(describeError(err, t)),
  });

  const toggle = (id: string, outstanding: string) =>
    setChosen((current) => {
      if (id in current) {
        const next = { ...current };
        delete next[id];
        return next;
      }
      return { ...current, [id]: outstanding };
    });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${t.banking.match} — ${formatDate(line.value_date)} · ${line.amount}`}
      closeLabel={t.common.close}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={Object.keys(chosen).length === 0}
            onClick={() => act.mutate()}
          >
            {t.banking.match}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Alert tone="info">{t.banking.matchHint}</Alert>

        {candidates.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t.banking.noCandidates}
            body={t.banking.noCandidatesHint}
          />
        ) : (
          <>
            {/*
              The candidate list gets its own scroll area: a busy day offers
              dozens of payments, and without a cap they push the fee field
              and the running total — the two things being decided — below
              the fold.
            */}
            <div className="max-h-72 overflow-y-auto">
              <Table>
                <thead>
                  <tr>
                    <Th>{t.banking.candidates}</Th>
                    <Th>{t.banking.valueDate}</Th>
                    <Th numeric>{t.banking.outstanding}</Th>
                    <Th numeric>{t.banking.amount}</Th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((candidate) => (
                    <tr key={candidate.id}>
                      <Td>
                        <Checkbox
                          label={candidate.label}
                          description={kindLabel[candidate.kind]}
                          checked={candidate.id in chosen}
                          onChange={() =>
                            toggle(candidate.id, candidate.outstanding)
                          }
                        />
                      </Td>
                      <Td>{formatDate(candidate.occurred_on)}</Td>
                      <Td numeric>{candidate.outstanding}</Td>
                      <Td numeric>
                        <Input
                          aria-label={`${candidate.label} ${t.banking.amount}`}
                          inputMode="decimal"
                          className="tabular text-right"
                          disabled={!(candidate.id in chosen)}
                          value={chosen[candidate.id] ?? ""}
                          onChange={(event) =>
                            setChosen((current) => ({
                              ...current,
                              [candidate.id]: event.target.value,
                            }))
                          }
                        />
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>

            <Field
              label={t.banking.commission}
              htmlFor="match-commission"
              hint={t.banking.commissionHint}
            >
              <Input
                id="match-commission"
                inputMode="decimal"
                className="tabular text-right"
                disabled={!onlyCardsChosen}
                value={commission}
                onChange={(event) => setCommission(event.target.value)}
              />
            </Field>

            <Field label={t.banking.note} htmlFor="match-note">
              <Input
                id="match-note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>

            {/*
              The arithmetic is shown before the match is sent, so an operator
              is not told what is wrong only after trying. The backend checks
              it again regardless.
            */}
            <Alert tone={accounted === unaccounted ? "success" : "warning"}>
              {t.banking.toExplain}: {line.unaccounted_amount} ·{" "}
              {t.banking.thisMatch}: {accounted.toFixed(2)}
            </Alert>
          </>
        )}
      </div>
    </Dialog>
  );
}

function IgnoreDialog({
  open,
  transactionId,
  onClose,
  onDone,
  onError,
}: {
  open: boolean;
  transactionId: string;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");

  const act = useMutation({
    mutationFn: () =>
      api.post<BankTransaction>(
        `/banking/transactions/${transactionId}/ignore`,
        { reason },
      ),
    onSuccess: () => {
      setReason("");
      onDone();
    },
    onError: (err) => onError(describeError(err, t)),
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.banking.setAside}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={reason.trim().length < 3}
            onClick={() => act.mutate()}
          >
            {t.common.confirm}
          </Button>
        </>
      }
    >
      <Field label={t.banking.setAsideReason} htmlFor="ignore-reason" required>
        <Input
          id="ignore-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Dialog>
  );
}
