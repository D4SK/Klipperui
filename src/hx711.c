// Communication with HX711 ADC
//
// Copyright (C) 2023 Konstantin Vogel <konstantin.vogel@gmx.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "autoconf.h" // CONFIG_*
#include "board/misc.h" // timer_read_time
#include "command.h" // DECL_COMMAND
#include "sched.h" // DECL_TASK
#include "basecmd.h" // oid_alloc
#include "board/gpio.h"
#include "load_cell_probe.h"

#define SAMPLE_INTERVAL (CONFIG_CLOCK_FREQ/80)
#define COMM_DELAY (40*(CONFIG_CLOCK_FREQ/1000000))
#define NO_DATA_RETRY_DELAY (200*(CONFIG_CLOCK_FREQ/1000000))

struct hx711 {
    uint32_t oid;
    int32_t sample;
    uint32_t sample_idx;
    uint32_t gain;
    struct timer timer;
    struct gpio_in dout;
    struct gpio_out sck;
    int32_t endstop_oid;
    int32_t result;
    int32_t invert;
    uint32_t active;
};


struct hx711 *hx711_1;
static struct task_wake hx711_wake;

static uint_fast8_t hx711_event(struct timer *timer)
{
    struct hx711 *h = hx711_1;
    uint32_t out = 0;
    uint32_t sample_complete = 0;
    uint32_t pin_state = gpio_in_read(h->dout);
    if (h->sample_idx == 0 && pin_state) {
        h->timer.waketime = timer_read_time() + NO_DATA_RETRY_DELAY;
        return SF_RESCHEDULE;
    }
    if (h->sample_idx % 2) {
        if (h->sample_idx < 48)
            h->sample |= pin_state << (31 - (h->sample_idx)/2);
        h->sample_idx++;
    }
    else {
        out = 1;
        h->sample_idx++;
    }
    if (h->sample_idx >= (48 + 2*h->gain)) {
        out = 0;
        h->result = h->sample >> 8;
        h->result *= h->invert;
        sched_wake_task(&hx711_wake);
        sample_complete = 1;
        h->sample_idx = 0;
        h->sample = 0;
    }
    gpio_out_write(h->sck, out);
    if (sample_complete) {
        if (h->active == 0)
            return SF_DONE;
        h->timer.waketime = timer_read_time() + SAMPLE_INTERVAL;
    } else {
        h->timer.waketime = timer_read_time() + COMM_DELAY;
    }
    return SF_RESCHEDULE;
}


void hx711_task(void)
{
    if (!sched_check_wake(&hx711_wake))
        return;
    struct hx711 *h = hx711_1;
    if (h->endstop_oid >= 0)
        check_load_cell_probe(h->endstop_oid, h->result, h->timer.waketime);
    sendf("hx711_in_state oid=%c clock=%u value=%i", h->oid, h->timer.waketime, h->result);
}
DECL_TASK(hx711_task);


void command_config_hx711(uint32_t *args)
{
    struct hx711 *h = oid_alloc(args[0], command_config_hx711, sizeof(*h));
    h->dout = gpio_in_setup(args[1], -1); // enable pulldown
    h->sck = gpio_out_setup(args[2], 1); // initialize as high
    h->gain = args[3];
    h->invert = args[4];
    h->sample_idx = 0;
    h->sample = 0;
    h->oid = args[0];
    h->timer.func = hx711_event;
    hx711_1 = h;
    h->active = 0;
}
DECL_COMMAND(command_config_hx711,
    "config_hx711 oid=%c dout_pin=%u sck_pin=%u gain=%u invert=%i");


void command_query_hx711(uint32_t *args)
{
    struct hx711 *h = oid_lookup(args[0], command_config_hx711);
    h->endstop_oid = args[2];
    h->active = args[1];
    if (h->active){
        sched_del_timer(&h->timer);
        h->sample_idx = 0;
        h->sample = 0;
        gpio_out_write(h->sck, 0);
        h->timer.waketime = timer_read_time() + NO_DATA_RETRY_DELAY;
        sched_add_timer(&h->timer);}
}
DECL_COMMAND(command_query_hx711, "query_hx711 oid=%c enable=%u endstop_oid=%i");
