function [x,y,nx,ny,tvector] = Smooth2dPos(x,y,CtrlVar)

%
%
%   [x,y,nx,ny] = Smooth2dPos(x,y,smoothing,CtrlVar)
%   Takes xy points defining a line in the plane
%   and returns a smooth line along (approximately) equally spaced points.
%   Obmitting the smoothing parameter implies no smoothing
%   Degree of smoothing is determined by CtrlVar.GLtension;
%   0<CtrlVar.GLtension<=1
%   CtrlVar.GLtension=1 implies `natural' smoothing, see help pages for csaps
%
% if points are not equally enough spaced, call Smooth2dPos again with
% CtrlVar.GLtension=1
% no smoothing, i.e: [x,y,nx,ny,tvector] = Smooth2dPos(x,y,CtrlVar)

if isempty(x) || isempty(y)
    
    x=[] ; y=[] ; nx=[] ; ny=[];
    return ;
    
end

I=~isnan(x) | ~isnan(y) ;
x=x(I);y=y(I);


if nargin < 3
    smoothing=1; % no smoothing
else
    smoothing=CtrlVar.GLtension;
end

x=x(:) ; y=y(:);
xy=[x';y']; df=diff(xy,1,2);

t = cumsum([0, sqrt([1 1]*(df.*df))]);
cv = csaps(t,xy,smoothing);


if nargin<3
    tvector=linspace(min(t),max(t),100) ;
else
    tvector=min(t):CtrlVar.GLds:max(t) ;
end

X=fnval(cv,tvector);
x=X(1,:) ; y=X(2,:);  x=x(:) ; y=y(:);

if nargout> 2
    % calculate normals to line
    fprime=fnder(cv,1);
    X=fnval(fprime,tvector);
    tx=X(1,:) ; ty=X(2,:);  tx=tx(:) ; ty=ty(:);
    nx=tx./sqrt(tx.*tx+ty.*ty);
    ny=ty./sqrt(tx.*tx+ty.*ty);
    temp=nx;
    nx=ny ; ny=-temp;
end


end

