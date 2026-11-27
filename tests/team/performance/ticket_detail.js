import { sleep } from 'k6';
import http from 'k6/http';
import { Rate } from 'k6/metrics';

const target = __ENV.SQE_K6_TARGET;
const ticketId = Number(__ENV.SQE_K6_TICKET_ID);
const vus = Number(__ENV.SQE_K6_VUS);
if (!/^http:\/\/(localhost|127\.0\.0\.1):\d+\/api\/tickets\/\d+\/$/.test(target)
    || ![1, 5].includes(vus) || !ticketId || !__ENV.SQE_K6_SESSION) {
    throw new Error('Use the localhost ticket-detail harness and its fixture session.');
}

const unexpected = new Rate('unexpected_failure');
http.setResponseCallback(http.expectedStatuses(200));

export const options = {
    scenarios: {
        ticket_detail: { executor: 'constant-vus', vus, duration: '65s' },
    },
    thresholds: {
        http_req_duration: ['p(95)<=500'],
        unexpected_failure: ['rate<0.01'],
    },
};

export default function () {
    const response = http.get(target, {
        redirects: 0,
        timeout: '5s',
        cookies: { [__ENV.SQE_K6_SESSION_NAME]: __ENV.SQE_K6_SESSION },
    });
    let passed = false;
    if (response.status === 200) {
        try {
            const ticket = response.json();
            passed = ticket.id === ticketId
                && ticket.title === __ENV.SQE_K6_TICKET_TITLE
                && ticket.status === Number(__ENV.SQE_K6_TICKET_STATUS);
        } catch {
            // Invalid response JSON counts as a failed retrieval.
        }
    }
    unexpected.add(!passed);
    sleep(1);
}

export function handleSummary(data) {
    return { [__ENV.SQE_K6_SUMMARY]: JSON.stringify(data, null, 2) };
}
