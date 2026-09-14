import {expect,it} from 'vitest';
import {parseRoute} from './router';
it.each(['/hr/conversations/old','/hr/positions/11111111-1111-4111-8111-111111111111/conversations/old','/hr/panorama/reports/11111111-1111-4111-8111-111111111111'])('retires %s',path=>expect(parseRoute(path)).toEqual({name:'not-found'}));
