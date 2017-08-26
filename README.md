## Simplified Production Process

Simplified production process was suggested first by @tara_antonius and we talked about it there:
https://discuss.erpnext.com/t/manufacturing-process/17013

SPP makes stock entries from BOMs to track a manufacture process without production orders.

The code comes from Production Planning Tool. Get orders from Material Request is not working for now. 


#### How to use

Select some ordes with the filters, click on "get items", and then "manufacture items"
Stock entries will be created and submited, RAW material moved from WIP warehouse to finished products warehouse.  

#### Installation

```shell
bench get-app spp https://github.com/hiousi/erpnext-spp
bench --site site1.local install-app spp `
```

#### License

MIT